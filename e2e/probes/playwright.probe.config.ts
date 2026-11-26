import { defineConfig, devices } from '@playwright/test'

// Masking probe only: no sandbox, no network, no storage state. It is not one of the
// 8 browser E2E tests and is expected to fail (see the workflow step that runs it).
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.probe\.ts$/,
  timeout: 15_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results-probe',
  use: { trace: 'off', screenshot: 'off', video: 'off' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
