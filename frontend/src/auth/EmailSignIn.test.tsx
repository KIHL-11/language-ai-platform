import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cloudAuth } from './client'
import { CloudSessionProvider } from './CloudSessionProvider'

vi.mock('../api/apiBase', () => ({ API_BASE_URL: '' }))
const json = (value: unknown, status = 200, headers: Record<string, string> = {}) => new Response(JSON.stringify(value), { status, headers })
const CHALLENGE = '0b8f2a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('Email sign-in', () => {
  function routes(verify: () => Response = () => json({ user_id: 'u_a', csrf_token: 'c'.repeat(64), created_account: true })) {
    vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
    let signedIn = false
    const mock = vi.fn((url: string, _init?: RequestInit) => {
      if (url === '/api/deployment') return Promise.resolve(json({ mode: 'cloud' }))
      if (url === '/api/account/providers') return Promise.resolve(json({ email: true, wechat: false }))
      if (url === '/api/account/me') return Promise.resolve(signedIn ? json({ user_id: 'u_a', csrf_token: 'c'.repeat(64), email: 'learner@example.test' }, 200, { 'X-Authenticated-User': 'u_a' }) : json({}, 401))
      if (url === '/api/account/email/start') return Promise.resolve(json({ challenge_id: CHALLENGE, expires_in: 600, resend_after: 60 }, 202))
      if (url === '/api/account/email/verify') { const response = verify(); signedIn = response.ok; return Promise.resolve(response) }
      return Promise.resolve(json({ items: [] }, 200, { 'X-Authenticated-User': 'u_a' }))
    })
    vi.stubGlobal('fetch', mock)
    return mock
  }

  it('requests a code, verifies it without identity claims and opens the account workspace', async () => {
    const mock = routes()
    const user = userEvent.setup()
    cloudAuth.invalidate()
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await user.type(await screen.findByLabelText('Email address'), 'learner@example.test')
    await user.click(screen.getByRole('button', { name: 'Send sign-in code' }))
    expect(await screen.findByText('learner@example.test')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Resend code \(60s\)/ })).toBeDisabled()
    await user.type(screen.getByLabelText('Sign-in code'), '12a3456')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Signed in as learner@example.test')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out everywhere' })).toBeInTheDocument()
    const calls = mock.mock.calls as unknown as [string, RequestInit | undefined][]
    const verify = calls.find(([url]) => url === '/api/account/email/verify')
    expect(verify).toBeDefined()
    const [, init] = verify as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({ challenge_id: CHALLENGE, code: '123456' })
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBeNull()
    expect(screen.queryByText('Personal')).not.toBeInTheDocument()
  })

  it('keeps the learner on the code step after a rejected code', async () => {
    routes(() => json({ detail: { code: 'email_code_invalid' } }, 400))
    const user = userEvent.setup()
    cloudAuth.invalidate()
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await user.type(await screen.findByLabelText('Email address'), 'learner@example.test')
    await user.click(screen.getByRole('button', { name: 'Send sign-in code' }))
    await user.type(await screen.findByLabelText('Sign-in code'), '000000')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid or has expired')
    expect(screen.getByLabelText('Sign-in code')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Use a different email' }))
    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
  })

  it('explains resend throttling', async () => {
    const mock = routes()
    mock.mockImplementation((url: string) => Promise.resolve(
      url === '/api/deployment' ? json({ mode: 'cloud' }) : url === '/api/account/providers' ? json({ email: true, wechat: false })
        : url === '/api/account/email/start' ? json({ detail: { code: 'email_login_throttled', retry_after: 42 } }, 429, { 'Retry-After': '42' }) : json({}, 401)))
    const user = userEvent.setup()
    cloudAuth.invalidate()
    render(<CloudSessionProvider><p>Personal</p></CloudSessionProvider>)
    await user.type(await screen.findByLabelText('Email address'), 'learner@example.test')
    await user.click(screen.getByRole('button', { name: 'Send sign-in code' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Try again in 42 seconds'))
  })
})
