import { expect, test } from '@playwright/test'

// Gate 2: fail closed when synthetic fixtures are absent. Never use real accounts.
const stateB = process.env.E2E_STORAGE_STATE_B
const itemA = process.env.E2E_ACCOUNT_A_LIBRARY_ITEM_ID
const replayState = process.env.E2E_STORAGE_STATE_REPLAY
if (!stateB || !itemA || !replayState) {
  throw new Error('Cloud security E2E requires E2E_STORAGE_STATE_B, E2E_ACCOUNT_A_LIBRARY_ITEM_ID and E2E_STORAGE_STATE_REPLAY (synthetic fixtures only)')
}

test('two authenticated accounts are isolated on private endpoints', async ({ page, browser, baseURL }) => {
  const target = new URL(baseURL!)
  const ignoreHTTPSErrors = process.env.E2E_DISPOSABLE_CI === 'yes' && target.origin === 'https://localhost:8443'
  const a = await page.request.get('/api/account/me')
  expect(a.status()).toBe(200)
  const idA = (await a.json()).user_id as string
  const own = await page.request.get('/api/library/items/' + encodeURIComponent(itemA))
  expect(own.status(), 'A must actually own an accessible seeded item').toBe(200)
  const bContext = await browser.newContext({ storageState: stateB, baseURL: target.origin, ignoreHTTPSErrors })
  try {
    const b = await bContext.request.get('/api/account/me')
    expect(b.status()).toBe(200)
    const idB = (await b.json()).user_id as string
    expect(idB).not.toBe(idA)

    const mismatch = await bContext.request.get('/api/study-bank/items', { params: { user_id: idA } })
    expect(mismatch.status(), 'B must not select A through user_id query').toBe(403)

    const crossItem = await bContext.request.get('/api/library/items/' + encodeURIComponent(itemA))
    expect(crossItem.status(), 'B must not read A Library item').toBe(404)
  } finally {
    await bContext.close()
  }
})

test('state-changing operation without CSRF is refused', async ({ page, baseURL }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  const response = await page.request.post('/api/account/session/rotate', {
    headers: { Origin: new URL(baseURL!).origin },
  })
  expect(response.status()).toBe(403)
  const body = await response.json()
  expect(body?.detail?.code).toBe('csrf_invalid')
})

test.describe('dedicated cookie replay session', () => {
  test.use({ storageState: replayState! })

  test('replay of revoked session cookie after logout is rejected', async ({ page, browser, baseURL }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  const oldCookies = await page.context().cookies()
  expect(oldCookies.filter(cookie => cookie.name === '__Host-language_ai_session')).toHaveLength(1)
  expect((await page.request.get('/api/account/me')).status()).toBe(200)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByText('Signed out of this browser view.')).toBeVisible()

  const replay = await browser.newContext({ baseURL, ignoreHTTPSErrors: process.env.E2E_DISPOSABLE_CI === 'yes' && baseURL === 'https://localhost:8443', storageState: { cookies: oldCookies, origins: [] } })
  try {
    const response = await replay.request.get('/api/account/me')
    expect(response.status(), 'Revoked cookie must not authorize a new context').toBe(401)
  } finally {
    await replay.close()
  }
})
})
