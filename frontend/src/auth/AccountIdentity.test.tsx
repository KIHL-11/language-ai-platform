import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cloudAuth } from './client'
import { CloudSessionProvider } from './CloudSessionProvider'
import { codeComplete, normalizeCode } from './codes'

vi.mock('../api/apiBase', () => ({ API_BASE_URL: '' }))
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'X-Authenticated-User': 'u_a' } })
const STATE = 's'.repeat(43)
const CHALLENGE = '0b8f2a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b'
type Call = [string, RequestInit | undefined]

class FakeChannel {
  static all: FakeChannel[] = []
  static sent: unknown[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  readonly name: string
  constructor(name: string) { this.name = name; FakeChannel.all.push(this) }
  postMessage(data: unknown) { FakeChannel.sent.push(data) }
  close() { FakeChannel.all = FakeChannel.all.filter((channel) => channel !== this) }
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); FakeChannel.all = []; FakeChannel.sent = [] })

function setup(routes: Record<string, (init?: RequestInit) => Response>, search = '') {
  const assign = vi.fn()
  vi.stubGlobal('location', { href: `https://learn.example/${search}`, origin: 'https://learn.example', protocol: 'https:', pathname: '/', search, assign })
  vi.stubGlobal('BroadcastChannel', FakeChannel)
  const replace = vi.spyOn(history, 'replaceState').mockImplementation(() => undefined)
  const mock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/deployment') return Promise.resolve(json({ mode: 'cloud' }))
    const route = routes[url]
    return Promise.resolve(route ? route(init) : json({ items: [] }))
  })
  vi.stubGlobal('fetch', mock)
  cloudAuth.invalidate()
  return { assign, replace, calls: () => mock.mock.calls as unknown as Call[] }
}

const me = (signedIn: () => boolean) => () => signedIn() ? json({ user_id: 'u_a', csrf_token: 'c'.repeat(64), email: 'learner@example.test' }) : json({}, 401)

describe('Sign-in codes', () => {
  it('accepts six digits or a long code typed in any case with dashes', () => {
    expect(normalizeCode('12a3456')).toBe('12A3456')
    expect(codeComplete('123456')).toBe(true)
    expect(codeComplete(normalizeCode('abcd-efgh-jk23'))).toBe(true)
    expect(codeComplete('12345')).toBe(false)
    expect(codeComplete('ABCD-EFGH')).toBe(false)
  })
})

describe('WeChat callback', () => {
  it('finishes a WeChat login, strips the code from the address bar and tells other tabs', async () => {
    let signedIn = false
    const { replace, calls } = setup({
      '/api/account/me': me(() => signedIn),
      '/api/account/providers': () => json({ email: true, wechat: true }),
      '/api/account/wechat/complete': () => { signedIn = true; return json({ purpose: 'login', user_id: 'u_a', csrf_token: 'c'.repeat(64) }) },
    }, `?oauth=wechat&code=synthetic-code-1&state=${STATE}`)
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    expect(await screen.findByText('Signed in as learner@example.test')).toBeInTheDocument()
    expect(replace).toHaveBeenCalledWith(null, '', '/')
    const complete = calls().find(([url]) => url === '/api/account/wechat/complete') as Call
    expect(JSON.parse(String(complete[1]?.body))).toEqual({ code: 'synthetic-code-1', state: STATE })
    expect(new Headers(complete[1]?.headers).get('X-CSRF-Token')).toBeNull()
    expect(FakeChannel.sent).toContain('signed-in')
  })

  it('explains an unlinked WeChat account and keeps the email form', async () => {
    setup({
      '/api/account/me': () => json({}, 401),
      '/api/account/providers': () => json({ email: true, wechat: true }),
      '/api/account/wechat/complete': () => json({ detail: { code: 'wechat_not_linked' } }, 409),
    }, `?oauth=wechat&code=synthetic-code-2&state=${STATE.replace(/^s/, 't')}`)
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    expect(await screen.findByRole('alert')).toHaveTextContent('not linked yet')
    expect(await screen.findByLabelText('Email address')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in with WeChat' })).toBeInTheDocument()
  })

  it('reports a cancelled WeChat approval without calling the server', async () => {
    const { calls } = setup({ '/api/account/me': () => json({}, 401), '/api/account/providers': () => json({ email: true, wechat: false }) }, '?oauth=wechat&state=x')
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    expect(await screen.findByRole('alert')).toHaveTextContent('cancelled')
    expect(calls().some(([url]) => url === '/api/account/wechat/complete')).toBe(false)
  })

  it('starts WeChat sign-in by leaving for the authorization URL', async () => {
    const { assign } = setup({
      '/api/account/me': () => json({}, 401),
      '/api/account/providers': () => json({ email: true, wechat: true }),
      '/api/account/wechat/start': () => json({ authorization_url: 'https://wechat.simulated.invalid/authorize?state=x' }),
    })
    const user = userEvent.setup()
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await user.click(await screen.findByRole('button', { name: 'Sign in with WeChat' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://wechat.simulated.invalid/authorize?state=x'))
  })
})

describe('Tab sync', () => {
  it('re-checks the account when another tab signs in', async () => {
    let signedIn = false
    setup({ '/api/account/me': me(() => signedIn), '/api/account/providers': () => json({ email: true, wechat: false }) })
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await screen.findByLabelText('Email address')
    signedIn = true
    act(() => { FakeChannel.all.forEach((channel) => channel.onmessage?.({ data: 'signed-in' } as MessageEvent)) })
    expect(await screen.findByText('Signed in as learner@example.test')).toBeInTheDocument()
  })
})

describe('Sign-in methods', () => {
  it('needs a fresh email proof and an explicit confirmation before linking WeChat', async () => {
    let fresh = 0
    const { assign, calls } = setup({
      '/api/account/me': me(() => true),
      '/api/account/identities': () => json({ identities: [{ provider: 'email', label: 'learner@example.test', linked_at: '2026-11-03T00:00:00+00:00' }], reauthenticated_for: fresh, wechat_available: true }),
      '/api/account/reauth/email/start': () => json({ challenge_id: CHALLENGE, email: 'learner@example.test', expires_in: 600, resend_after: 60 }, 202),
      '/api/account/reauth/email/verify': () => { fresh = 600; return json({ reauthenticated_for: 600 }) },
      '/api/account/wechat/link/start': () => json({ authorization_url: 'https://wechat.simulated.invalid/authorize?state=y' }),
    })
    const user = userEvent.setup()
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await user.click(await screen.findByRole('button', { name: 'Sign-in methods' }))
    expect(await screen.findByText('Email: learner@example.test', { exact: false })).toBeInTheDocument()
    const proceed = screen.getByRole('button', { name: 'Continue to WeChat' })
    expect(proceed).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Send confirmation code' }))
    await user.type(await screen.findByLabelText('Confirmation code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Confirm email' }))
    await screen.findByText(/Email confirmed/)
    expect(screen.getByRole('button', { name: 'Continue to WeChat' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Continue to WeChat' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://wechat.simulated.invalid/authorize?state=y'))
    const linkStart = calls().find(([url]) => url === '/api/account/wechat/link/start') as Call
    expect(JSON.parse(String(linkStart[1]?.body))).toEqual({ confirm: 'link-wechat' })
    expect(new Headers(linkStart[1]?.headers).get('X-CSRF-Token')).toBe('c'.repeat(64))
  })

  it('removes WeChat only after confirmation and explains a refused last method', async () => {
    let wechat = true
    setup({
      '/api/account/me': me(() => true),
      '/api/account/identities': () => json({ identities: [{ provider: 'email', label: 'learner@example.test', linked_at: '2026-11-03T00:00:00+00:00' }, ...(wechat ? [{ provider: 'wechat', label: 'WeChat', linked_at: '2026-11-03T00:00:00+00:00' }] : [])], reauthenticated_for: 300, wechat_available: true }),
      '/api/account/identities/unlink': () => { wechat = false; return new Response(null, { status: 204 }) },
    })
    const user = userEvent.setup()
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await user.click(await screen.findByRole('button', { name: 'Sign-in methods' }))
    const remove = await screen.findByRole('button', { name: 'Remove WeChat' })
    expect(remove).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(remove)
    expect(await screen.findByRole('heading', { name: 'Link WeChat' })).toBeInTheDocument()
  })

  it('shows the linked result after returning from WeChat', async () => {
    setup({
      '/api/account/me': me(() => true),
      '/api/account/wechat/complete': () => json({ purpose: 'link' }),
      '/api/account/identities': () => json({ identities: [{ provider: 'wechat', label: 'WeChat', linked_at: '2026-11-03T00:00:00+00:00' }], reauthenticated_for: 0, wechat_available: true }),
      '/api/account/identities/unlink': () => json({ detail: { code: 'last_login_method' } }, 422),
    }, `?oauth=wechat&code=synthetic-code-3&state=${STATE.replace(/^s/, 'u')}`)
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    expect(await screen.findByText('WeChat is now linked to your account.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sign-in methods' })).toBeInTheDocument()
  })
})
