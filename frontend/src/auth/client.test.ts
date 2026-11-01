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
