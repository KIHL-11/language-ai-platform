// Regression tests for scripts/verify_evidence.mjs. Run: node --test scripts/verify_evidence.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const verifier = join(dirname(fileURLToPath(import.meta.url)), 'verify_evidence.mjs')
const PRE = ['cloud-api-responses', 'logout-http', 'two-account-isolation', 'csrf-refusal', 'cookie-replay', 'media-provenance', 'email-relogin-cross-tab']
const TOKEN = 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCd'.slice(0, 43)

function record(code = 'ok') { return { evidence: 'x', records: [{ path: '/api/account/me', method: 'GET', status: 200, code }] } }

// A complete, valid run: 8 evidence files and four storage states with one live token.
function fixture() {
  const base = mkdtempSync(join(tmpdir(), 'evidence-test-'))
  const evidence = join(base, 'evidence')
  for (const name of PRE) write(join(evidence, 'pre-restart', name + '.json'), record())
  write(join(evidence, 'post-restart', 'restart-persistence.json'), record())
  const env = { E2E_EVIDENCE_DIR: evidence }
  for (const [variable, file] of [['E2E_STORAGE_STATE', 'read'], ['E2E_STORAGE_STATE_B', 'b'], ['E2E_STORAGE_STATE_LOGOUT', 'logout'], ['E2E_STORAGE_STATE_REPLAY', 'replay']]) {
    const path = join(base, 'state', file + '.json')
    write(path, { cookies: [{ name: '__Host-language_ai_session', value: file === 'read' ? TOKEN : TOKEN.slice(0, 42) + file[0] }] })
    env[variable] = path
  }
  return { base, evidence, env }
}
function write(path, value) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, typeof value === 'string' ? value : JSON.stringify(value))
}
function run(env, ...args) {
  const clean = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('E2E_') && key !== 'CLOUD_AUTH_CODE_KEY'))
  return spawnSync(process.execPath, [verifier, ...args], { env: { ...clean, ...env }, encoding: 'utf8' })
}
function expectDeleted(t, result, evidence) {
  assert.equal(result.status, 1, result.stderr)
  assert.equal(existsSync(evidence), false, 'scan failure must delete the evidence directory')
}

test('scan passes and keeps verified evidence', () => {
  const { evidence, env } = fixture()
  const result = run(env, '--scan-only')
  assert.equal(result.status, 0, result.stderr)
  assert.equal(existsSync(join(evidence, 'pre-restart', 'csrf-refusal.json')), true)
})

test('phase checks pass on complete evidence', () => {
  const { env } = fixture()
  assert.equal(run(env, 'pre-restart').status, 0)
  assert.equal(run(env, 'post-restart').status, 0)
})

test('malformed storage-state JSON fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  writeFileSync(env.E2E_STORAGE_STATE, '{not json')
  expectDeleted(t, run(env, '--scan-only'), evidence)
})

test('a missing storage-state file fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  expectDeleted(t, run({ ...env, E2E_STORAGE_STATE: join(evidence, '..', 'absent.json') }, '--scan-only'), evidence)
})

test('an unreadable evidence entry fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  mkdirSync(join(evidence, 'pre-restart', 'directory.json'))
  expectDeleted(t, run(env, '--scan-only'), evidence)
})

test('a permission-denied evidence file fails the scan and deletes evidence', (t) => {
  if (process.getuid?.() === 0) { t.skip('root can read any file'); return }
  const { evidence, env } = fixture()
  const file = join(evidence, 'pre-restart', 'logout-http.json')
  chmodSync(file, 0o000)
  expectDeleted(t, run(env, '--scan-only'), evidence)
})

test('malformed evidence JSON fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  write(join(evidence, 'pre-restart', 'logout-http.json'), '{"records": [')
  expectDeleted(t, run(env, '--scan-only'), evidence)
})

for (const [label, code] of [
  ['a live session token of this run', TOKEN],
  ['a 64-hex key', 'a'.repeat(64)],
  ['an email address', 'person@example.test'],
  ['a cookie field', null],
]) {
  test(`${label} fails the scan and deletes evidence`, (t) => {
    const { evidence, env } = fixture()
    const body = code === null ? { records: [{ path: '/p', status: 200 }], cookies: [] } : record(code)
    write(join(evidence, 'pre-restart', 'logout-http.json'), body)
    expectDeleted(t, run(env, '--scan-only'), evidence)
  })
}

test('the auth code key fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  write(join(evidence, 'pre-restart', 'logout-http.json'), record('K3Y-material'))
  expectDeleted(t, run({ ...env, CLOUD_AUTH_CODE_KEY: 'K3Y-material' }, '--scan-only'), evidence)
})

test('a trace, screenshot or other non-JSON file fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  write(join(evidence, 'pre-restart', 'trace.zip'), 'PK')
  expectDeleted(t, run(env, '--scan-only'), evidence)
})

test('an unexpected directory fails the scan and deletes evidence', (t) => {
  const { evidence, env } = fixture()
  write(join(evidence, 'storage', 'read.json'), {})
  expectDeleted(t, run(env, '--scan-only'), evidence)
})

test('an empty or missing evidence directory fails the scan', () => {
  const { base, env } = fixture()
  assert.equal(run({ ...env, E2E_EVIDENCE_DIR: join(base, 'absent') }, '--scan-only').status, 1)
  const empty = join(base, 'empty'); mkdirSync(empty)
  assert.equal(run({ ...env, E2E_EVIDENCE_DIR: empty }, '--scan-only').status, 1)
  assert.equal(run({ ...env, E2E_EVIDENCE_DIR: '' }, '--scan-only').status, 1)
})

test('phase check fails on a missing file or an extra field', () => {
  const { evidence, env } = fixture()
  write(join(evidence, 'pre-restart', 'csrf-refusal.json'), { records: [{ path: '/p', status: 403, token: 'x' }] })
  assert.equal(run(env, 'pre-restart').status, 1)
  const second = fixture()
  write(join(second.evidence, 'post-restart', 'restart-persistence.json'), { records: [] })
  assert.equal(run(second.env, 'post-restart').status, 1)
})
