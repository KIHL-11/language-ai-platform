import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL
const storageState = process.env.E2E_STORAGE_STATE

// Explicit preflight: never silently run against localhost or production.
if (!baseURL || !storageState || process.env.E2E_ISOLATED_SANDBOX !== 'yes') {
  throw new Error('E2E requires E2E_BASE_URL, E2E_STORAGE_STATE and E2E_ISOLATED_SANDBOX=yes')
}
const url = new URL(baseURL)
const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || (url.protocol !== 'https:' && !(isLocal && url.protocol === 'http:' && process.env.E2E_ALLOW_LOCAL === 'yes'))) {
  throw new Error('E2E target requires HTTPS, except explicitly permitted loopback HTTP; no userinfo, path, query or fragment')
}
if (isLocal && process.env.E2E_ALLOW_LOCAL !== 'yes') {
  throw new Error('Local sandbox targets require E2E_ALLOW_LOCAL=yes')
}
const expectedHost = process.env.E2E_EXPECTED_HOST
if (!expectedHost || expectedHost !== url.host) {
  throw new Error('E2E_EXPECTED_HOST must exactly match the verified sandbox host (including port)')
}
if (!isLocal && !/(^|[.-])(sandbox|test)([.-]|$)/i.test(url.hostname) && process.env.E2E_ALLOW_NONSTANDARD_HOST !== 'yes') {
  throw new Error('Sandbox host not recognized; require separate E2E_ALLOW_NONSTANDARD_HOST=yes acknowledgment')
}
export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  outputDir: 'test-results',
  use: {
    baseURL,
    storageState,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
