// Fails unless every expected sanitized evidence file exists, parses as JSON and
// contains no secret material.
//   node scripts/verify_evidence.mjs pre-restart|post-restart   completeness + content
//   node scripts/verify_evidence.mjs --scan-only                 content of whatever exists;
//     on any violation the evidence directory is deleted so nothing unsafe is uploaded.
import { readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const EXPECTED = {
  'pre-restart': [
    'cloud-api-responses', 'logout-http', 'two-account-isolation', 'csrf-refusal',
    'cookie-replay', 'media-provenance', 'email-relogin-cross-tab',
  ],
  'post-restart': ['restart-persistence'],
}
function fail(message) { console.error(`Evidence check failed: ${message}`); process.exit(1) }

const phase = process.argv[2]
const root = process.env.E2E_EVIDENCE_DIR
const scanOnly = phase === '--scan-only'
if (scanOnly && !root) { console.log('Evidence scan: no evidence directory configured (job failed before setup)'); process.exit(0) }
if (!root || !(scanOnly || phase in EXPECTED)) fail('usage: E2E_EVIDENCE_DIR=<dir> verify_evidence.mjs pre-restart|post-restart|--scan-only')

// Secrets that exist in this run and must never appear in evidence.
const secrets = []
for (const variable of ['E2E_STORAGE_STATE', 'E2E_STORAGE_STATE_B', 'E2E_STORAGE_STATE_LOGOUT', 'E2E_STORAGE_STATE_REPLAY']) {
  const path = process.env[variable]
  if (!path) continue
  try {
    for (const cookie of JSON.parse(readFileSync(path, 'utf8')).cookies ?? []) if (cookie.value) secrets.push(cookie.value)
  } catch { fail(`${variable} cannot be read to check evidence for its session token`) }
}
if (process.env.CLOUD_AUTH_CODE_KEY) secrets.push(process.env.CLOUD_AUTH_CODE_KEY)
const forbidden = [
  [/__Host-language_ai_session/, 'session cookie name'],
  [/set-cookie|"cookies?"\s*:|\bcookie:/i, 'cookie header or cookie field'],
  [/\b[a-f0-9]{64}\b/, '64-hex token (CSRF or key)'],
  [/(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])/, 'session-token-shaped value'],
  [/@/, 'email address'],
  [/Code:|sign-in code|otp/i, 'sign-in code'],
  [/synthetic-e2e-[ab]/, 'synthetic owner id'],
  [/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i, 'uuid identifier'],
]

function violations(text) {
  const found = []
  for (const secret of secrets) if (secret && text.includes(secret)) found.push('a live secret')
  for (const [pattern, label] of forbidden) if (pattern.test(text)) found.push(label)
  return found
}

if (scanOnly) {
  let problems = 0
  for (const sub of Object.keys(EXPECTED)) {
    let names = []
    try { names = readdirSync(join(root, sub)) } catch { continue }
    for (const name of names) {
      const found = violations(readFileSync(join(root, sub, name), 'utf8'))
      if (found.length) { problems += 1; console.error(`Unsafe evidence ${sub}/${name}: ${found.join(', ')}`) }
    }
  }
  if (problems) { rmSync(root, { recursive: true, force: true }); fail('unsafe evidence was deleted before upload') }
  console.log('Evidence scan OK: no secret material in any evidence file')
  process.exit(0)
}

const directory = join(root, phase)
let present
try { present = new Set(readdirSync(directory)) } catch { fail(`missing evidence directory ${phase}`) }
const summary = []
for (const name of EXPECTED[phase]) {
  const file = name + '.json'
  if (!present.has(file)) fail(`missing ${phase}/${file}`)
  const text = readFileSync(join(directory, file), 'utf8')
  let parsed
  try { parsed = JSON.parse(text) } catch { fail(`${phase}/${file} is not valid JSON`) }
  if (!Array.isArray(parsed.records) || parsed.records.length === 0) fail(`${phase}/${file} has no HTTP records`)
  for (const record of parsed.records) {
    const keys = Object.keys(record).sort().join(',')
    if (!/^(code,)?(created,)?(method,)?path,status$/.test(keys)) fail(`${phase}/${file} has unexpected fields: ${keys}`)
    if (!Number.isInteger(record.status)) fail(`${phase}/${file} has a non-integer status`)
  }
  const found = violations(text)
  if (found.length) fail(`${phase}/${file} contains ${found.join(', ')}`)
  summary.push({ file, records: parsed.records.length })
}
const extra = [...present].filter((file) => !EXPECTED[phase].includes(file.replace(/\.json$/, '')) && file !== 'manifest.json')
if (extra.length) fail(`unexpected files in ${phase}: ${extra.join(', ')}`)
writeFileSync(join(directory, 'manifest.json'), JSON.stringify({
  phase,
  app_sha: process.env.E2E_APP_SHA ?? null,
  harness_sha: process.env.E2E_HARNESS_SHA ?? null,
  files: summary,
}, null, 2) + '\n')
console.log(`Evidence OK (${phase}): ${summary.map((entry) => `${entry.file}=${entry.records}`).join(' ')}`)
