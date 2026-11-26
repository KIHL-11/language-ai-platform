import { test } from '@playwright/test'
import { maskInActionsLog } from '../tests/secrets.js'

// Reproduces the exposure path with a synthetic value: a fill() into a disabled input
// times out, and Playwright prints `fill("<value>")` in the failure call log. The value
// is masked first, exactly as login() masks the real sign-in code.
test('a failed fill() of a masked value', async ({ page }) => {
  const value = process.env.E2E_MASK_PROBE_VALUE
  if (!value) throw new Error('E2E_MASK_PROBE_VALUE is required')
  maskInActionsLog(value)
  await page.setContent('<label>Sign-in code <input aria-label="Sign-in code" disabled></label>')
  await page.getByRole('textbox', { name: 'Sign-in code' }).fill(value, { timeout: 2_000 })
})
