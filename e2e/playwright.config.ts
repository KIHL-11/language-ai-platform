import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL
const storageState = process.env.E2E_STORAGE_STATE

// Explicit preflight: never silently run against localhost or production.
if (!baseURL || !storageState || process.env.E2E_ISOLATED_SANDBOX !== 'yes') {
  throw new Error('E2E requires E2E_BASE_URL, E2E_STORAGE_STATE and E2E_ISOLATED_SANDBOX=yes')
}
const url = new URL(baseURL)
if (!['http:', 'https:'].includes(url.protocol) || url.hostname === 'localhost' || url.hostname === '127.0.0.1' && process.env.E2E_ALLOW_LOCAL !== 'yes') {
  throw new Error('E2E target must be an explicit isolated sandbox (local targets require E2E_ALLOW_LOCAL=yes)')
}
if (!/sandbox|test|127\.0\.0\.1|localhost/.test(url.hostname) && process.env.E2E_ALLOW_NONSTANDARD_HOST !== 'yes') {
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
