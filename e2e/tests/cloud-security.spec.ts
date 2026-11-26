import { expect, test } from '@playwright/test'
import { recordEvidence, type EvidenceRecord } from './evidence.js'

// Gate 2: fail closed when synthetic fixtures are absent. Never use real accounts.
const stateB = process.env.E2E_STORAGE_STATE_B
const itemA = process.env.E2E_ACCOUNT_A_LIBRARY_ITEM_ID
const replayState = process.env.E2E_STORAGE_STATE_REPLAY
if (!stateB || !itemA || !replayState) {
  throw new Error('Cloud security E2E requires E2E_STORAGE_STATE_B, E2E_ACCOUNT_A_LIBRARY_ITEM_ID and E2E_STORAGE_STATE_REPLAY (synthetic fixtures only)')
}

test('two authenticated accounts are isolated on private endpoints', async ({ page, browser, baseURL }, testInfo) => {
  const records: EvidenceRecord[] = []
  try {
  const target = new URL(baseURL!)
  const ignoreHTTPSErrors = process.env.E2E_DISPOSABLE_CI === 'yes' && target.origin === 'https://localhost:8443'
  const a = await page.request.get('/api/account/me')
  records.push({ path: '/api/account/me', method: 'GET', status: a.status(), code: 'account-a' })
  expect(a.status()).toBe(200)
  const idA = (await a.json()).user_id as string
  const own = await page.request.get('/api/library/items/' + encodeURIComponent(itemA))
  records.push({ path: '/api/library/items/{id}', method: 'GET', status: own.status(), code: 'owner-a-reads-own-item' })
  expect(own.status(), 'A must actually own an accessible seeded item').toBe(200)
  const bContext = await browser.newContext({ storageState: stateB, baseURL: target.origin, ignoreHTTPSErrors })
  try {
    const b = await bContext.request.get('/api/account/me')
    records.push({ path: '/api/account/me', method: 'GET', status: b.status(), code: 'account-b' })
    expect(b.status()).toBe(200)
    const idB = (await b.json()).user_id as string
    expect(idB).not.toBe(idA)

    const mismatch = await bContext.request.get('/api/study-bank/items', { params: { user_id: idA } })
    records.push({ path: '/api/study-bank/items', method: 'GET', status: mismatch.status(), code: 'b-selects-a-by-user-id' })
    expect(mismatch.status(), 'B must not select A through user_id query').toBe(403)

    const crossItem = await bContext.request.get('/api/library/items/' + encodeURIComponent(itemA))
    records.push({ path: '/api/library/items/{id}', method: 'GET', status: crossItem.status(), code: 'b-reads-a-item' })
    expect(crossItem.status(), 'B must not read A Library item').toBe(404)
  } finally {
    await bContext.close()
  }
  } finally {
    await recordEvidence(testInfo, 'two-account-isolation', records)
  }
})

test('state-changing operation without CSRF is refused', async ({ page, baseURL }, testInfo) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  const response = await page.request.post('/api/account/session/rotate', {
    headers: { Origin: new URL(baseURL!).origin },
  })
  const body = await response.json().catch(() => null)
  await recordEvidence(testInfo, 'csrf-refusal', [{ path: '/api/account/session/rotate', method: 'POST', status: response.status(), code: (body?.detail?.code ?? null) as string | null }])
  expect(response.status()).toBe(403)
  expect(body?.detail?.code).toBe('csrf_invalid')
})

test.describe('dedicated cookie replay session', () => {
  test.use({ storageState: replayState! })

  test('replay of revoked session cookie after logout is rejected', async ({ page, browser, baseURL }, testInfo) => {
  const records: EvidenceRecord[] = []
  try {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  const oldCookies = await page.context().cookies()
  expect(oldCookies.filter(cookie => cookie.name === '__Host-language_ai_session')).toHaveLength(1)
  const before = await page.request.get('/api/account/me')
  records.push({ path: '/api/account/me', method: 'GET', status: before.status(), code: 'before-logout' })
  expect(before.status()).toBe(200)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByText('Signed out of this browser view.')).toBeVisible()

  const replay = await browser.newContext({ baseURL, ignoreHTTPSErrors: process.env.E2E_DISPOSABLE_CI === 'yes' && baseURL === 'https://localhost:8443', storageState: { cookies: oldCookies, origins: [] } })
  try {
    const response = await replay.request.get('/api/account/me')
    records.push({ path: '/api/account/me', method: 'GET', status: response.status(), code: 'revoked-cookie-replay' })
    expect(response.status(), 'Revoked cookie must not authorize a new context').toBe(401)
  } finally {
    await replay.close()
  }
  } finally {
    await recordEvidence(testInfo, 'cookie-replay', records)
  }
})
})
