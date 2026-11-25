import { expect, test } from '@playwright/test'

// Synthetic authenticated sandbox only. No test provisions accounts or runs migrations.
test('Cloud Library → Words & Chunks: Study Bank request and UI succeed', async ({ page }, testInfo) => {
  const calls: Array<{ path: string; status: number; code: unknown }> = []
  const pending: Promise<void>[] = []
  page.on('response', (response) => {
    const url = new URL(response.url())
    if (!url.pathname.startsWith('/api/')) return
    const capture = (async () => {
      let code: unknown = null
      if (response.status() >= 400) {
        try { code = (await response.json())?.detail?.code ?? null } catch { /* non-JSON */ }
      }
      calls.push({ path: url.pathname, status: response.status(), code })
    })()
    pending.push(capture)
  })
  try {
    await page.goto('/')
    await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
    await page.getByRole('button', { name: 'My Library' }).click()
    await expect(page.getByRole('heading', { name: 'My Library' })).toBeVisible()
    const studyResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/study-bank/items'
    )
    await page.getByRole('button', { name: 'Words & Chunks' }).click()
    await expect(page.getByRole('heading', { name: 'Words & Chunks' })).toBeVisible()
    const response = await studyResponse
    expect(response.status(), 'Study Bank GET returned an error').toBe(200)
    await expect(page.getByText('Loading Words & Chunks…')).toHaveCount(0)
    await expect(page.getByRole('alert').filter({ hasText: 'Words & Chunks is unavailable' })).toHaveCount(0)
    // A 200 response alone is insufficient: the UI must parse and display the data.
    await expect(page.getByText('No active words yet.').or(page.locator('main ul > li').first())).toBeVisible()
  } finally {
    await Promise.allSettled(pending)
    await testInfo.attach('cloud-api-responses.json', {
      body: JSON.stringify(calls, null, 2),
      contentType: 'application/json',
    })
  }
})

const logoutState = process.env.E2E_STORAGE_STATE_LOGOUT
if (!logoutState) throw new Error('E2E_STORAGE_STATE_LOGOUT is required: destructive logout must use a dedicated synthetic session')

test.describe('dedicated logout session', () => {
  test.use({ storageState: logoutState })

  test('Sign out: server confirmation and reload must not restore private view', async ({ page }, testInfo) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  const logoutResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/account/logout' &&
    response.request().method() === 'POST'
  )
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  const response = await logoutResponse
  let code: unknown = null
  if (!response.ok()) {
    try { code = (await response.json())?.detail?.code ?? null } catch { /* non-JSON */ }
  }
  await testInfo.attach('logout-http.json', {
    body: JSON.stringify({ path: '/api/account/logout', status: response.status(), code }, null, 2),
    contentType: 'application/json',
  })
  expect(response.status(), 'Logout POST must be confirmed by server').toBe(204)
  await expect(page.getByText('Signed out of this browser view.')).toBeVisible()
  await expect(page.getByText('Local view cleared, but server logout was not confirmed.')).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toHaveCount(0)
  await expect(page.getByRole('status').filter({ hasText: /session is missing or expired|Signed out of this browser view/ })).toBeVisible()
})
})
