import { expect, test, type Page } from '@playwright/test'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

function requireDisposableCI(baseURL: string | undefined) {
  if (process.env.E2E_DISPOSABLE_CI !== 'yes' || baseURL !== 'https://localhost:8443') {
    throw new Error('Advanced destructive and mailbox cases require the dedicated loopback CI sandbox')
  }
}

test('Library-backed Study Bank write is permitted and idempotent (#91)', async ({ page, baseURL }, testInfo) => {
  requireDisposableCI(baseURL)
  const fixture = process.env.E2E_MEDIA_STUDY_INPUT
  if (!fixture) throw new Error('Missing synthetic media study fixture')
  const input = JSON.parse(readFileSync(fixture, 'utf8'))
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  const submit = () => page.evaluate(async (body) => {
    const me = await fetch('/api/account/me', { credentials: 'same-origin' })
    if (!me.ok) return { status: me.status, code: 'account_me_failed', created: null }
    const { csrf_token } = await me.json()
    const response = await fetch('/api/study-bank/items/bulk', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf_token },
      body: JSON.stringify(body),
    })
    const result = await response.json().catch(() => ({}))
    return { status: response.status, code: result.detail?.code ?? null, created: result.created ?? null }
  }, input)
  const first = await submit()
  await testInfo.attach('media-provenance-first.json', { body: JSON.stringify(first), contentType: 'application/json' })
  expect(first.status).toBe(200)
  expect(first.created).toBe(1)
  const again = await submit()
  await testInfo.attach('media-provenance-repeat.json', { body: JSON.stringify(again), contentType: 'application/json' })
  expect(again.status).toBe(200)
  expect(again.created).toBe(0)
  await page.getByRole('button', { name: 'Chunks', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'figure it out', exact: true })).toBeVisible()
})

async function login(page: Page, email: string) {
  const directory = process.env.CLOUD_EMAIL_SANDBOX_DIR
  if (!directory) throw new Error('Missing private sandbox mailbox')
  const prior = new Set(readdirSync(directory))
  await page.getByRole('textbox', { name: 'Email address' }).fill(email)
  await page.getByRole('button', { name: 'Send sign-in code' }).click()
  await expect(page.getByRole('textbox', { name: 'Sign-in code' })).toBeVisible()
  let code = ''
  await expect.poll(() => {
    for (const name of readdirSync(directory)) {
      if (prior.has(name)) continue
      const body = readFileSync(join(directory, name), 'ascii')
      if (body.startsWith('To: ' + email + '\n')) code = body.match(/^Code: ([A-Z0-9-]+)$/m)?.[1] ?? ''
    }
    return Boolean(code)
  }, { timeout: 10000 }).toBe(true)
  await page.getByRole('textbox', { name: 'Sign-in code' }).fill(code)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
}

test.describe('real sandbox email login', () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  test('email sign-in, sign-out, same-account re-login and unconfirmed cross-tab logout (#92)', async ({ page, baseURL }) => {
    requireDisposableCI(baseURL)
    test.setTimeout(120_000)
    const email = 'e2e-fresh-account@example.test'
    await page.goto('/')
    await login(page, email)
    const first = await page.request.get('/api/account/me')
    expect(first.status()).toBe(200)
    const identity = (await first.json()).user_id
    expect(typeof identity).toBe('string')
    await page.getByRole('button', { name: 'Sign out', exact: true }).click()
    await expect(page.getByText('Signed out of this browser view.')).toBeVisible()
    // The service enforces a 60-second per-email resend interval; use the real clock.
    await page.waitForTimeout(61_000)
    await login(page, email)
    const second = await page.request.get('/api/account/me')
    expect(second.status()).toBe(200)
    expect((await second.json()).user_id).toBe(identity)

    const other = await page.context().newPage()
    await other.goto('/')
    await expect(other.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
    await page.route('**/api/account/logout', route => route.abort('failed'))
    await page.getByRole('button', { name: 'Sign out', exact: true }).click()
    await expect(page.getByText('Local view cleared, but server logout was not confirmed.')).toBeVisible()
    await expect(other.getByText('另一个标签页尝试退出，但服务器未确认。当前会话可能仍然有效。')).toBeVisible()
    await expect(other.getByRole('navigation', { name: 'Cloud learning' })).toHaveCount(0)
    expect((await other.request.get('/api/account/me')).status()).toBe(200)
    await other.getByRole('button', { name: '重试退出' }).click()
    await expect(other.getByText('Signed out of this browser view.')).toBeVisible()
    expect((await other.request.get('/api/account/me')).status()).toBe(401)
    await other.close()
  })
})

test('after service restart: persistent synthetic session and Library-backed chunk remain accessible', async ({ page, baseURL }) => {
  requireDisposableCI(baseURL)
  const item = process.env.E2E_ACCOUNT_A_LIBRARY_ITEM_ID
  if (!item) throw new Error('Missing seeded item ID')
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Cloud learning' })).toBeVisible()
  expect((await page.request.get('/api/library/items/' + encodeURIComponent(item))).status()).toBe(200)
  await page.getByRole('button', { name: 'Chunks', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'figure it out', exact: true })).toBeVisible()
})
