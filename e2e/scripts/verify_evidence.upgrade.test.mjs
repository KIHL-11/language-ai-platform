// Upgrade-phase regression tests for scripts/verify_evidence.mjs (audio/outbox
// evidence of the integration candidate). The original 23 tests are unchanged.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const verifier = join(dirname(fileURLToPath(import.meta.url)), 'verify_evidence.mjs')
const ok = { records: [{ path: '/db/cloud_media_cleanup_outbox', method: 'CHECK', status: 200, code: 'retired-object-queued', created: 1 }] }

function write(path, value) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(value))
}
function fixture() {
  const evidence = join(mkdtempSync(join(tmpdir(), 'evidence-upgrade-')), 'evidence')
  write(join(evidence, 'upgrade', 'audio-outbox.json'), { records: [{ path: '/api/library/items/{id}/audio', method: 'GET', status: 200, code: 'owner-audio' }] })
  write(join(evidence, 'upgrade', 'outbox-retention.json'), ok)
  write(join(evidence, 'upgrade-restart', 'upgrade-restart-audio.json'), { records: [{ path: '/api/library/items/{id}/audio', method: 'GET', status: 404, code: 'deleted-after-restart' }] })
  return evidence
}
function run(evidence, ...args) {
  const clean = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('E2E_')))
  return spawnSync(process.execPath, [verifier, ...args], {
    env: { ...clean, E2E_EVIDENCE_DIR: evidence, E2E_DB_PROFILE: '0014', E2E_APP_TREE: 'a'.repeat(40) }, encoding: 'utf8',
  })
}

test('upgrade phases pass on complete evidence and record the database profile', () => {
  const evidence = fixture()
  assert.equal(run(evidence, 'upgrade').status, 0)
  assert.equal(run(evidence, 'upgrade-restart').status, 0)
  const manifest = JSON.parse(readFileSync(join(evidence, 'upgrade', 'manifest.json'), 'utf8'))
  assert.equal(manifest.db_profile, '0014')
  assert.equal(manifest.app_tree, 'a'.repeat(40))
  assert.equal(run(evidence, '--scan-only').status, 0)
})

test('missing outbox-retention evidence fails the upgrade phase', () => {
  const evidence = fixture()
  const result = spawnSync('rm', [join(evidence, 'upgrade', 'outbox-retention.json')])
  assert.equal(result.status, 0)
  assert.equal(run(evidence, 'upgrade').status, 1)
})

test('an object key or owner id in upgrade evidence fails the scan and deletes evidence', () => {
  const evidence = fixture()
  write(join(evidence, 'upgrade', 'outbox-retention.json'), { records: [{ path: '/media/synthetic-e2e-a/x', method: 'CHECK', status: 200 }] })
  const result = run(evidence, '--scan-only')
  assert.equal(result.status, 1)
  assert.equal(existsSync(evidence), false)
})
