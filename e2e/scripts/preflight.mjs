import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const variables = {
  read: 'E2E_STORAGE_STATE',
  accountB: 'E2E_STORAGE_STATE_B',
  logout: 'E2E_STORAGE_STATE_LOGOUT',
  replay: 'E2E_STORAGE_STATE_REPLAY',
}
function fail(message) { throw new Error(`E2E preflight: ${message}`) }
if (process.env.E2E_ISOLATED_SANDBOX !== 'yes') fail('explicit isolation acknowledgement is required')
const target = process.env.E2E_BASE_URL
if (!target) fail('E2E_BASE_URL is required')
let origin
try { origin = new URL(target) } catch { fail('E2E_BASE_URL is invalid') }
if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
  fail('cloud E2E requires an exact HTTPS sandbox origin')
}
if (!process.env.E2E_EXPECTED_HOST || process.env.E2E_EXPECTED_HOST !== origin.host) fail('E2E_EXPECTED_HOST must match the HTTPS sandbox host')
if (!/(^|[.-])(sandbox|test)([.-]|$)/i.test(origin.hostname) && process.env.E2E_ALLOW_NONSTANDARD_HOST !== 'yes') {
  fail('nonstandard sandbox hostname needs explicit override and independent verification')
}
if (!process.env.E2E_ACCOUNT_A_LIBRARY_ITEM_ID) fail('a seeded synthetic account A Library item ID is required')
const sessions = new Map()
const files = new Set()
for (const [role, variable] of Object.entries(variables)) {
  const path = process.env[variable]
  if (!path) fail(`${variable} is required`)
  const absolute = resolve(path)
  if (files.has(absolute)) fail('storage-state file paths must be distinct')
  files.add(absolute)
  let state
  try { state = JSON.parse(readFileSync(absolute, 'utf8')) } catch { fail(`${variable} must point to readable JSON`) }
  if (!state || !Array.isArray(state.cookies)) fail(`${variable} must contain a Playwright cookies array`)
  const cookies = state.cookies.filter(cookie => cookie.name === '__Host-language_ai_session')
  if (cookies.length !== 1) fail(`${variable} must contain exactly one Cloud session cookie`)
  const cookie = cookies[0]
  if (cookie.domain !== origin.hostname || cookie.path !== '/' || cookie.secure !== true || cookie.httpOnly !== true) {
    fail(`${variable} session cookie does not match HTTPS sandbox host and secure cookie attributes`)
  }
  if (typeof cookie.value !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(cookie.value)) fail(`${variable} session token shape invalid`)
  if (sessions.has(cookie.value)) fail(`${variable} reuses the ${sessions.get(cookie.value)} session cookie`)
  sessions.set(cookie.value, role)
}
console.log('E2E preflight OK: exact HTTPS sandbox origin, synthetic fixture, four distinct session cookies')
