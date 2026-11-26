import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const script = fileURLToPath(new URL('./verify_evidence.mjs', import.meta.url))
const fixtures = [
  ['valid evidence', (root, phase) => writeFileSync(join(phase, 'csrf-refusal.json'), '{"records":[{"path":"/api/account/session/rotate","status":403}]}'), 0],
  ['malformed storage state', (root, phase, env) => { const p = join(root, 'state.json'); writeFileSync(p, '{'); env.E2E_STORAGE_STATE = p }, 1],
  ['unexpected root file', (root, phase) => writeFileSync(join(root, 'unexpected.json'), '{}'), 1],
  ['unexpected phase file', (root, phase) => writeFileSync(join(phase, 'unknown.json'), '{}'), 1],
  ['malformed evidence', (root, phase) => writeFileSync(join(phase, 'csrf-refusal.json'), '{'), 1],
  ['symlink evidence', (root, phase) => symlinkSync(join(root, 'missing'), join(phase, 'csrf-refusal.json')), 1],
  ['secret material', (root, phase) => writeFileSync(join(phase, 'csrf-refusal.json'), '{"code":"synthetic-e2e-a"}'), 1],
]
for (const [name, setup, status] of fixtures) test(name, () => {
  const parent = mkdtempSync(join(tmpdir(), 'e2e-evidence-check-'))
  const root = join(parent, 'evidence')
  const phase = join(root, 'pre-restart')
  mkdirSync(phase, { recursive: true })
  const env = { ...process.env, E2E_EVIDENCE_DIR: root }
  for (const key of ['E2E_STORAGE_STATE','E2E_STORAGE_STATE_B','E2E_STORAGE_STATE_LOGOUT','E2E_STORAGE_STATE_REPLAY','CLOUD_AUTH_CODE_KEY']) delete env[key]
  try {
    setup(root, phase, env)
    const result = spawnSync(process.execPath, [script, '--scan-only'], { env, encoding: 'utf8' })
    assert.equal(result.status, status, result.stderr)
  } finally { rmSync(parent, { recursive: true, force: true }) }
})
