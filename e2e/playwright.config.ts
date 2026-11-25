import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL
const storageState = process.env.E2E_STORAGE_STATE

// Explicit preflight: never silently run against production; Cloud requires HTTPS.
if (!baseURL || !storageState || process.env.E2E_ISOLATED_SANDBOX !== 'yes') {
  throw new Error('E2E requires E2E_BASE_URL, E2E_STORAGE_STATE and E2E_ISOLATED_SANDBOX=yes')
}
const url = new URL(baseURL)
if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
  throw new Error('Cloud E2E must target an exact HTTPS origin')
}
if (!process.env.E2E_EXPECTED_HOST || process.env.E2E_EXPECTED_HOST !== url.host) {
  throw new Error('E2E_EXPECTED_HOST must exactly match verified sandbox host')
}
if (!/(^|[.-])(sandbox|test)([.-]|$)/i.test(url.hostname) && process.env.E2E_ALLOW_NONSTANDARD_HOST !== 'yes') {
  throw new Error('Nonstandard sandbox hostname requires independent verification')
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
