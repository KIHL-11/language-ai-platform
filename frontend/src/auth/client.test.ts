import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CloudAuthClient } from './client'

const csrf = 'a'.repeat(64)
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })

describe('Cloud authentication client', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
    vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset()
  })
  afterEach(() => vi.unstubAllGlobals())

  async function authenticated(client = new CloudAuthClient()) {
    fetchMock.mockResolvedValueOnce(json({ mode: 'cloud' })).mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: csrf }))
    await client.bootstrap('')
    return client
  }

  it('requires server deployment metadata and uses only server identity', async () => {
    localStorage.setItem('language_ai_learner_id_v1', 'untrusted-local-id')
    const client = await authenticated()
    expect(client.getSnapshot()).toMatchObject({ status: 'authenticated', userId: 'owner-a' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/deployment')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ credentials: 'same-origin', redirect: 'error' })
    expect(localStorage.getItem('language_ai_learner_id_v1')).toBe('untrusted-local-id')
    expect(JSON.stringify(localStorage)).not.toContain(csrf)
  })

  it.each([401, 503])('fails closed when account/me returns %s', async (status) => {
    fetchMock.mockResolvedValueOnce(json({ mode: 'cloud' })).mockResolvedValueOnce(json({}, status))
    const client = new CloudAuthClient(); await client.bootstrap('')
    expect(client.getSnapshot().status).toBe(status === 401 ? 'expired' : 'unavailable')
    await expect(client.request('/api/study-bank/items')).rejects.toMatchObject({ status: 401 })
  })

  it('refuses cross-origin bootstrap and credential-bearing requests', async () => {
    fetchMock.mockResolvedValueOnce(json({ mode: 'cloud' }))
    const client = new CloudAuthClient(); await client.bootstrap('https://other.example')
    expect(client.getSnapshot().status).toBe('unavailable')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const valid = await authenticated()
    await expect(valid.request('https://other.example/api/items', { method: 'POST' })).rejects.toMatchObject({ status: 403 })
  })

  it.each(['POST', 'PATCH', 'DELETE'])('adds ephemeral CSRF and same-origin credentials for %s', async (method) => {
    const client = await authenticated()
    fetchMock.mockResolvedValueOnce(json({ ok: true }))
    await client.request('/api/items', { method, headers: { 'Content-Type': 'application/json' } })
    const [, init] = fetchMock.mock.calls.at(-1)!
    expect(init.credentials).toBe('same-origin'); expect(init.redirect).toBe('error')
    expect(init.headers.get('X-CSRF-Token')).toBe(csrf)
  })

  it.each([401, 403])('clears identity/CSRF after %s without retry', async (status) => {
    const client = await authenticated(); fetchMock.mockResolvedValueOnce(json({}, status))
    await expect(client.request('/api/items', { method: 'POST' })).rejects.toMatchObject({ status })
    expect(client.getSnapshot().status).toBe(status === 401 ? 'expired' : 'csrf-invalid')
    const count = fetchMock.mock.calls.length
    await expect(client.request('/api/items', { method: 'POST' })).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(count)
  })

  it('rejects results from the previous identity even after response headers arrived', async () => {
    const client = await authenticated()
    let resolve!: (response: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done }))
    const pending = client.request('/api/items')
    fetchMock.mockResolvedValueOnce(json({ user_id: 'owner-b', csrf_token: 'b'.repeat(64) }))
    await client.refresh()
    resolve(json({ private: 'owner-a' }))
    await expect(pending).rejects.toMatchObject({ status: 409 })
    expect(client.getSnapshot()).toMatchObject({ userId: 'owner-b' })
  })

  it('refreshes CSRF after rotation and clears state on logout', async () => {
    const client = await authenticated()
    fetchMock.mockResolvedValueOnce(json({ csrf_token: 'b'.repeat(64) })).mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: 'b'.repeat(64) }))
    await client.rotate()
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await client.logout()
    expect(fetchMock.mock.calls.at(-1)![1].headers.get('X-CSRF-Token')).toBe('b'.repeat(64))
    expect(client.getSnapshot().status).toBe('logged-out')
  })

  it('retains Personal Desktop request behavior only after personal metadata', async () => {
    fetchMock.mockResolvedValueOnce(json({ mode: 'personal' }))
    const client = new CloudAuthClient(); await client.bootstrap('http://127.0.0.1:8000')
    fetchMock.mockResolvedValueOnce(json({}))
    await client.request('http://127.0.0.1:8000/api/items')
    expect(fetchMock.mock.calls.at(-1)).toEqual(['http://127.0.0.1:8000/api/items'])
  })

  it('refuses a private GET response under a changed cookie owner before exposing its body', async () => {
    const client = await authenticated()
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ private: 'owner-b library' }), { headers: { 'X-Authenticated-User': 'owner-b' } }))
    await expect(client.request('/api/library/items')).rejects.toMatchObject({ status: 409 })
    expect(client.getSnapshot().status).toBe('expired')
  })

  it('fails closed when a private response has no trustworthy owner binding', async () => {
    const client = await authenticated()
    fetchMock.mockResolvedValueOnce(json({ items: [] }))
    await expect(client.request('/api/study-bank/items?user_id=owner-a')).rejects.toMatchObject({ status: 409 })
  })

  it('shows unconfirmed revocation after network logout failure and blocks further writes', async () => {
    const client = await authenticated()
    fetchMock.mockRejectedValueOnce(new Error('network unavailable'))
    await expect(client.logout()).rejects.toThrow('network unavailable')
    expect(client.getSnapshot().status).toBe('logout-unconfirmed')
    await expect(client.request('/api/items', { method: 'POST' })).rejects.toMatchObject({ status: 401 })
  })

  it('keeps the logout outcome when its own cross-tab invalidate arrives in the same tab', async () => {
    // The workspace broadcasts 'invalidate' after logout; BroadcastChannel also
    // delivers it to this tab's own listener, which calls invalidate().
    const confirmed = await authenticated()
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await confirmed.logout()
    confirmed.invalidate()
    expect(confirmed.getSnapshot().status).toBe('logged-out')

    const unconfirmed = await authenticated()
    fetchMock.mockRejectedValueOnce(new Error('network unavailable'))
    await expect(unconfirmed.logout()).rejects.toThrow('network unavailable')
    unconfirmed.invalidate()
    expect(unconfirmed.getSnapshot().status).toBe('logout-unconfirmed')

    const otherTab = await authenticated()
    otherTab.invalidate()
    expect(otherTab.getSnapshot().status).toBe('expired')
  })

  it('does not let an old logout finalizer clear a newer authenticated identity', async () => {
    const client = await authenticated()
    let resolve!: (response: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done }))
    const pending = client.logout()
    fetchMock.mockResolvedValueOnce(json({ user_id: 'owner-b', csrf_token: 'b'.repeat(64) }))
    await client.refresh()
    resolve(new Response(null, { status: 204 }))
    await expect(pending).rejects.toMatchObject({ status: 409 })
    expect(client.getSnapshot()).toMatchObject({ status: 'authenticated', userId: 'owner-b' })
  })
})

describe('Cross-tab logout outcomes', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
    vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset()
  })
  afterEach(() => vi.unstubAllGlobals())

  // One client per browser tab; messages are delivered by the test like BroadcastChannel would.
  async function tab() {
    const client = new CloudAuthClient()
    fetchMock.mockResolvedValueOnce(json({ mode: 'cloud' })).mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: csrf }))
    await client.bootstrap('')
    return client
  }
  const deliver = (kind: string, ...tabs: CloudAuthClient[]) => tabs.forEach((t) => t.receive(kind))

  it('single tab: its own confirmed or unconfirmed message does not change its outcome', async () => {
    const confirmed = await tab()
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await confirmed.logout()
    deliver('logout-confirmed', confirmed)
    expect(confirmed.getSnapshot().status).toBe('logged-out')

    const unconfirmed = await tab()
    fetchMock.mockRejectedValueOnce(new Error('network unavailable'))
    await expect(unconfirmed.logout()).rejects.toThrow('network unavailable')
    deliver('logout-unconfirmed', unconfirmed)
    expect(unconfirmed.getSnapshot().status).toBe('logout-unconfirmed')
  })

  it('two tabs: a confirmed logout invalidates the other tab', async () => {
    const first = await tab(); const second = await tab()
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await first.logout()
    deliver('logout-confirmed', first, second)
    expect(first.getSnapshot().status).toBe('logged-out')
    expect(second.getSnapshot().status).toBe('expired')
  })

  it.each([
    ['network failure', () => fetchMock.mockRejectedValueOnce(new Error('network unavailable'))],
    ['HTTP 503', () => fetchMock.mockResolvedValueOnce(json({ detail: { code: 'unavailable' } }, 503))],
  ])('two tabs, %s: the other tab pauses instead of claiming the session ended', async (_label, fail) => {
    const first = await tab(); const second = await tab()
    fail()
    await expect(first.logout()).rejects.toBeDefined()
    deliver('logout-unconfirmed', first, second)
    expect(first.getSnapshot().status).toBe('logout-unconfirmed')
    expect(second.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
    const calls = fetchMock.mock.calls.length
    await expect(second.request('/api/library/items', { method: 'POST' })).rejects.toMatchObject({ status: 401 })
    await expect(second.request('/api/library/items')).rejects.toMatchObject({ status: 401 })
    expect(fetchMock.mock.calls.length).toBe(calls)  // paused: nothing reached the network
    // A later confirmation resolves both tabs.
    deliver('logout-confirmed', first, second)
    expect(first.getSnapshot().status).toBe('logged-out')
    expect(second.getSnapshot().status).toBe('expired')
  })

  it('session check from the paused tab: 200 resumes, 401 ends, an unreachable server keeps the warning', async () => {
    const paused = await tab()
    deliver('logout-unconfirmed', paused)
    fetchMock.mockRejectedValueOnce(new Error('network unavailable'))
    await paused.checkSession()
    expect(paused.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
    fetchMock.mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: csrf }))
    await paused.checkSession()
    expect(paused.getSnapshot()).toMatchObject({ status: 'authenticated', userId: 'owner-a' })

    deliver('logout-unconfirmed', paused)
    fetchMock.mockResolvedValueOnce(json({ detail: { code: 'authentication_invalid' } }, 401))
    await paused.checkSession()
    expect(paused.getSnapshot().status).toBe('expired')
  })

  it('retried logout from the paused tab: confirmed, already ended, refused again, or unreachable', async () => {
    const confirmed = await tab(); deliver('logout-unconfirmed', confirmed)
    fetchMock.mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: csrf })).mockResolvedValueOnce(new Response(null, { status: 204 }))
    await confirmed.retryLogout()
    expect(confirmed.getSnapshot().status).toBe('logged-out')
    expect(fetchMock.mock.calls.at(-1)![1].headers.get('X-CSRF-Token')).toBe(csrf)

    const ended = await tab(); deliver('logout-unconfirmed', ended)
    fetchMock.mockResolvedValueOnce(json({}, 401))
    await ended.retryLogout()
    expect(ended.getSnapshot().status).toBe('logged-out')

    const refused = await tab(); deliver('logout-unconfirmed', refused)
    fetchMock.mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: csrf })).mockResolvedValueOnce(json({}, 503))
    await expect(refused.retryLogout()).rejects.toBeDefined()
    expect(refused.getSnapshot().status).toBe('logout-unconfirmed')

    const unreachable = await tab(); deliver('logout-unconfirmed', unreachable)
    fetchMock.mockRejectedValueOnce(new Error('network unavailable'))
    await expect(unreachable.retryLogout()).rejects.toMatchObject({ status: 503 })
    expect(unreachable.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
  })

  it('the previous build\'s generic invalidate never overrides a logout outcome', async () => {
    const unconfirmed = await tab()
    fetchMock.mockRejectedValueOnce(new Error('network unavailable'))
    await expect(unconfirmed.logout()).rejects.toThrow()
    deliver('invalidate', unconfirmed)
    expect(unconfirmed.getSnapshot().status).toBe('logout-unconfirmed')
    const other = await tab()
    deliver('invalidate', other)
    expect(other.getSnapshot().status).toBe('expired')
  })
})

describe('Unconfirmed logout gate: refresh in flight and retried logout (Codex delta A1/A2)', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
    vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset()
  })
  afterEach(() => vi.unstubAllGlobals())
  function deferred<T>() {
    let resolve!: (value: T) => void; let reject!: (reason: unknown) => void
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
  }
  async function tab() {
    const client = new CloudAuthClient()
    fetchMock.mockResolvedValueOnce(json({ mode: 'cloud' })).mockResolvedValueOnce(json({ user_id: 'owner-a', csrf_token: csrf }))
    await client.bootstrap('')
    return client
  }
  const me = () => json({ user_id: 'owner-a', csrf_token: 'b'.repeat(64) })

  it.each([
    ['a late 200', (d: ReturnType<typeof deferred<Response>>) => d.resolve(me())],
    ['a late network error', (d: ReturnType<typeof deferred<Response>>) => d.reject(new Error('network unavailable'))],
  ])('A1: an unconfirmed logout during a /me refresh wins over %s', async (_label, settle) => {
    const client = await tab()
    const pending = deferred<Response>()
    fetchMock.mockReturnValueOnce(pending.promise)
    const refresh = client.refresh()
    expect(client.getSnapshot().status).toBe('loading')
    const signal = fetchMock.mock.calls.at(-1)![1].signal as AbortSignal
    const generation = client.getSnapshot().generation
    client.receive('logout-unconfirmed')
    expect(signal.aborted).toBe(true)
    expect(client.getSnapshot()).toMatchObject({ status: 'logout-unconfirmed-elsewhere' })
    expect(client.getSnapshot().generation).toBeGreaterThan(generation)
    settle(pending)
    await refresh
    expect(client.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
    await expect(client.request('/api/library/items')).rejects.toMatchObject({ status: 401 })
  })

  it('A1: a session check that is still loading also stays gated when another unconfirmed logout arrives', async () => {
    const client = await tab()
    client.receive('logout-unconfirmed')
    const pending = deferred<Response>()
    fetchMock.mockReturnValueOnce(pending.promise)
    const check = client.checkSession()
    expect(client.getSnapshot().status).toBe('loading')
    client.receive('logout-unconfirmed')
    pending.resolve(me())
    await check
    expect(client.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
  })

  // A2: the retry never publishes 'authenticated'; while the logout is in flight no learning
  // request can run, and only the logout outcome changes the state.
  it.each([
    ['200', () => new Response(null, { status: 200 }), 'logged-out'],
    ['204', () => new Response(null, { status: 204 }), 'logged-out'],
    ['401', () => json({}, 401), 'logged-out'],
    ['503', () => json({}, 503), 'logout-unconfirmed'],
  ])('A2: retried logout answered %s stays gated until the answer arrives', async (_label, answer, outcome) => {
    const client = await tab()
    client.receive('logout-unconfirmed')
    const seen: string[] = []
    client.subscribe(() => seen.push(client.getSnapshot().status))
    const logout = deferred<Response>()
    fetchMock.mockResolvedValueOnce(me()).mockReturnValueOnce(logout.promise)
    const retry = client.retryLogout().catch((error) => error)
    await vi.waitFor(() => expect(fetchMock.mock.calls.at(-1)![0]).toBe('/api/account/logout'))
    expect(fetchMock.mock.calls.at(-1)![1].headers.get('X-CSRF-Token')).toBe('b'.repeat(64))
    expect(client.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
    const calls = fetchMock.mock.calls.length
    await expect(client.request('/api/library/items')).rejects.toMatchObject({ status: 401 })
    await expect(client.request('/api/library/items', { method: 'POST', body: '{}' })).rejects.toMatchObject({ status: 401 })
    expect(fetchMock.mock.calls.length).toBe(calls)
    logout.resolve(answer())
    await retry
    expect(client.getSnapshot().status).toBe(outcome)
    expect(seen).not.toContain('authenticated')
    expect(seen).not.toContain('loading')
  })

  it('A2: a network failure on the retried logout keeps the outcome unconfirmed and never authenticates', async () => {
    const client = await tab()
    client.receive('logout-unconfirmed')
    const seen: string[] = []
    client.subscribe(() => seen.push(client.getSnapshot().status))
    fetchMock.mockResolvedValueOnce(me()).mockRejectedValueOnce(new Error('network unavailable'))
    await expect(client.retryLogout()).rejects.toThrow('network unavailable')
    expect(client.getSnapshot().status).toBe('logout-unconfirmed')
    expect(seen).toEqual(['logout-unconfirmed'])
  })

  it('A2: a concurrent broadcast during the retry wins and the late logout answer is dropped', async () => {
    const client = await tab()
    client.receive('logout-unconfirmed')
    const logout = deferred<Response>()
    fetchMock.mockResolvedValueOnce(me()).mockReturnValueOnce(logout.promise)
    const retry = client.retryLogout().catch((error) => error)
    await vi.waitFor(() => expect(fetchMock.mock.calls.at(-1)![0]).toBe('/api/account/logout'))
    const signal = fetchMock.mock.calls.at(-1)![1].signal as AbortSignal
    client.receive('logout-unconfirmed')  // ignored: already gated, retry continues
    expect(client.getSnapshot().status).toBe('logout-unconfirmed-elsewhere')
    client.receive('logout-confirmed')    // another tab confirmed the logout first
    expect(client.getSnapshot().status).toBe('expired')
    expect(signal.aborted).toBe(true)
    logout.resolve(json({}, 503))
    await retry
    expect(client.getSnapshot().status).toBe('expired')
  })
})
