import { expect, test } from '@playwright/test'

// Use ONLY seeded synthetic account state. Nothing in these tests creates users,
// mutates production data, or runs database migrations.
test('Cloud Library → Words & Chunks does not return HTTP 503', async ({ page }, testInfo) => {
  const calls: Array<{ url: string; status: number; code: unknown }> = []
  page.on('response', async (response) => {
    const url = response.url()
    if (!url.includes('/api/') && !/study.bank|library/i.test(url)) return
    let code: unknown = null
    if (response.status() >= 400) {
      try { code = (await response.json())?.detail?.code ?? null } catch { /* non-JSON error */ }
    }
    calls.push({ url, status: response.status(), code })
  })
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  await page.getByRole('button', { name: 'My Library' }).click()
  await expect(page.getByRole('heading', { name: 'My Library' })).toBeVisible()
  await page.getByRole('button', { name: 'Words & Chunks' }).click()
  await expect(page.getByRole('heading', { name: 'Words & Chunks' })).toBeVisible()
  await expect.poll(() => page.getByText('Loading Words & Chunks…').count()).toBe(0)
  await testInfo.attach('cloud-api-responses.json', {
    body: JSON.stringify(calls, null, 2),
    contentType: 'application/json',
  })
  expect(calls.some((call) => /study.bank|study_bank/i.test(call.url)), 'No Study Bank API response was observed').toBe(true)
  expect(calls.filter((call) => call.status === 503), 'Unexpected HTTP 503').toEqual([])
  await expect(page.getByText('Words & Chunks is unavailable. Please try again.')).toHaveCount(0)
})

test('Sign out reports confirmed result, not unconfirmed server logout', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByText('Signed out of this browser view.')).toBeVisible()
  await expect(page.getByText('Local view cleared, but server logout was not confirmed.')).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toHaveCount(0)
})
