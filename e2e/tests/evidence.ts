import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { TestInfo } from '@playwright/test'

// Sanitized HTTP evidence. Only allow-listed scalar fields are written: request
// path, method, HTTP status, an error `detail.code`, and counters. Nothing that
// could carry a cookie, CSRF token, sign-in code, email address or identifier.
type Scalar = string | number | boolean | null
export type EvidenceRecord = { path: string; method?: string; status: number; code?: Scalar; created?: Scalar }

const SAFE_STRING = /^[A-Za-z0-9_./{}-]{0,80}$/

function clean(record: EvidenceRecord): EvidenceRecord {
  const out: EvidenceRecord = { path: record.path, status: record.status }
  if (record.method !== undefined) out.method = record.method
  if (record.code !== undefined) out.code = record.code
  if (record.created !== undefined) out.created = record.created
  // Library item ids and other path parameters are replaced, so paths stay generic.
  out.path = out.path.replace(/\/api\/library\/items\/[^/]+(\/audio)?$/, '/api/library/items/{id}$1')
  // Anything that is not a short plain token is redacted rather than written.
  for (const key of ['path', 'method', 'code'] as const) {
    const value = out[key]
    if (typeof value === 'string' && !SAFE_STRING.test(value)) out[key] = '[redacted]'
  }
  return out
}

// Writes <E2E_EVIDENCE_DIR>/<phase>/<name>.json and attaches the same body to the
// Playwright report. A missing directory is a configuration error, not a skip.
export async function recordEvidence(testInfo: TestInfo, name: string, records: EvidenceRecord[]) {
  const root = process.env.E2E_EVIDENCE_DIR
  const phase = process.env.E2E_EVIDENCE_PHASE
  if (!root || !phase || !/^[a-z-]+$/.test(phase) || !/^[a-z0-9-]+$/.test(name)) {
    throw new Error('E2E_EVIDENCE_DIR and E2E_EVIDENCE_PHASE are required for sanitized evidence')
  }
  // The test title is not written: titles are free text and are already in the report.
  const body = JSON.stringify({ evidence: name, records: records.map(clean) }, null, 2)
  const directory = join(root, phase)
  mkdirSync(directory, { recursive: true, mode: 0o700 })
  writeFileSync(join(directory, name + '.json'), body + '\n', { mode: 0o600 })
  await testInfo.attach(name + '.json', { body, contentType: 'application/json' })
}
