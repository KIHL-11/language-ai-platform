import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { recordEvidence, type EvidenceRecord } from './evidence.js'

// #97 audio regression, browser side. Runs in its own workflow steps (tags
// @upgrade / @upgrade-restart) under both database profiles. The database side
// (outbox row pending, bytes retained) is checked by scripts/verify_outbox_retention.py.

type Fixture = { keep: { item_id: string; sha256: string }; retire: { item_id: string; sha256: string } }

function fixture(baseURL: string | undefined): Fixture {
  if (process.env.E2E_DISPOSABLE_CI !== 'yes' || baseURL !== 'https://localhost:8443') {
    throw new Error('Audio/outbox cases require the dedicated loopback CI sandbox')
  }
  const path = process.env.E2E_OUTBOX_FIXTURE
  if (!path) throw new Error('Missing synthetic outbox fixture')
  return JSON.parse(readFileSync(path, 'utf8'))
}

// Digests are compared as a boolean with a fixed message, so a mismatch never prints
// the expected or received value into the job log.
const sha256 = (body: Buffer) => createHash('sha256').update(body).digest('hex')

test('Library audio stays referenced; deleting an item queues its audio instead of unlinking it (#97) @upgrade', async ({ page, browser, baseURL }, testInfo) => {
  const data = fixture(baseURL)
  const keepPath = '/api/library/items/' + encodeURIComponent(data.keep.item_id) + '/audio'
  const retireItem = '/api/library/items/' + encodeURIComponent(data.retire.item_id)
  const records: EvidenceRecord[] = []
  const other = await browser.newContext({ storageState: process.env.E2E_STORAGE_STATE_B })
  try {
    await page.goto('/')
    await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()

    const keep = await page.request.get(keepPath)
    records.push({ path: keepPath, method: 'GET', status: keep.status(), code: 'owner-audio' })
    expect(keep.status()).toBe(200)
    expect(sha256(await keep.body()) === data.keep.sha256, 'keep audio bytes match the seeded fixture').toBe(true)

    const retireAudio = await page.request.get(retireItem + '/audio')
    records.push({ path: retireItem + '/audio', method: 'GET', status: retireAudio.status(), code: 'before-delete' })
    expect(retireAudio.status()).toBe(200)
    expect(sha256(await retireAudio.body()) === data.retire.sha256, 'retire audio bytes match the seeded fixture').toBe(true)

    const foreign = await other.request.get(keepPath)
    records.push({ path: keepPath, method: 'GET', status: foreign.status(), code: 'other-account' })
    expect(foreign.status()).toBe(404)

    const deleted = await page.evaluate(async (path) => {
      const me = await fetch('/api/account/me', { credentials: 'same-origin' })
      if (!me.ok) return me.status
      const { csrf_token } = await me.json()
      const response = await fetch(path, {
        method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf_token },
      })
      return response.status
    }, retireItem)
    records.push({ path: retireItem, method: 'DELETE', status: deleted, code: 'delete-item-with-audio' })
    expect(deleted).toBe(204)

    const afterAudio = await page.request.get(retireItem + '/audio')
    records.push({ path: retireItem + '/audio', method: 'GET', status: afterAudio.status(), code: 'after-delete' })
    expect(afterAudio.status()).toBe(404)
    const afterItem = await page.request.get(retireItem)
    records.push({ path: retireItem, method: 'GET', status: afterItem.status(), code: 'after-delete' })
    expect(afterItem.status()).toBe(404)

    const stillKept = await page.request.get(keepPath)
    records.push({ path: keepPath, method: 'GET', status: stillKept.status(), code: 'kept-after-delete' })
    expect(stillKept.status()).toBe(200)
    expect(sha256(await stillKept.body()) === data.keep.sha256, 'keep audio bytes match the seeded fixture').toBe(true)
  } finally {
    await other.close()
    await recordEvidence(testInfo, 'audio-outbox', records)
  }
})

test('upgrade-restart: referenced audio survives the restart and deleted audio stays unavailable @upgrade-restart', async ({ page, browser, baseURL }, testInfo) => {
  const data = fixture(baseURL)
  const keepPath = '/api/library/items/' + encodeURIComponent(data.keep.item_id) + '/audio'
  const retirePath = '/api/library/items/' + encodeURIComponent(data.retire.item_id) + '/audio'
  const records: EvidenceRecord[] = []
  const other = await browser.newContext({ storageState: process.env.E2E_STORAGE_STATE_B })
  try {
    await page.goto('/')
    await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
    const keep = await page.request.get(keepPath)
    records.push({ path: keepPath, method: 'GET', status: keep.status(), code: 'owner-audio-after-restart' })
    expect(keep.status()).toBe(200)
    expect(sha256(await keep.body()) === data.keep.sha256, 'keep audio bytes match the seeded fixture').toBe(true)
    const retired = await page.request.get(retirePath)
    records.push({ path: retirePath, method: 'GET', status: retired.status(), code: 'deleted-after-restart' })
    expect(retired.status()).toBe(404)
    const foreign = await other.request.get(keepPath)
    records.push({ path: keepPath, method: 'GET', status: foreign.status(), code: 'other-account-after-restart' })
    expect(foreign.status()).toBe(404)
  } finally {
    await other.close()
    await recordEvidence(testInfo, 'upgrade-restart-audio', records)
  }
})
