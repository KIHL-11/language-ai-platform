export type AuthState =
  | { status: 'loading' | 'unavailable' | 'expired' | 'csrf-invalid' | 'logged-out' | 'logout-unconfirmed'; generation: number }
  | { status: 'personal'; generation: number }
  | { status: 'authenticated'; userId: string; email: string | null; generation: number }

export class CloudAuthError extends Error {
  readonly status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export class CloudAuthClient {
  private state: AuthState = { status: 'loading', generation: 0 }
  private csrf: string | null = null
  private controller = new AbortController()
  private listeners = new Set<() => void>()
  private cloud = false
  invalidate() { this.clear('expired') }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  private publish(state: AuthState) { this.state = state; this.listeners.forEach((listener) => listener()) }
  private clear(status: 'loading' | 'unavailable' | 'expired' | 'csrf-invalid' | 'logged-out' | 'logout-unconfirmed') {
    this.controller.abort(); this.controller = new AbortController(); this.csrf = null
    this.publish({ status, generation: this.state.generation + 1 })
  }
  // Called only after trusted deployment metadata, or by isolated test setup.
  usePersonal() { this.cloud = false; this.clear('loading'); this.publish({ status: 'personal', generation: this.state.generation }) }
  async bootstrap(baseUrl: string) {
    this.clear('loading')
    const generation = this.state.generation
    try {
      const config = await fetch(`${baseUrl}/api/deployment`, { credentials: 'omit', redirect: 'error', cache: 'no-store', signal: this.controller.signal })
      if (!config.ok) throw new Error('Deployment unavailable')
      const metadata = await config.json() as { mode?: unknown }
      if (generation !== this.state.generation) return
      if (metadata.mode === 'personal' && !this.cloud) { this.usePersonal(); return }
      if (metadata.mode !== 'cloud') throw new Error('Invalid deployment')
      this.cloud = true
      if (new URL(baseUrl || '/', location.href).origin !== location.origin || location.protocol !== 'https:') throw new Error('Cloud requires same-origin HTTPS')
      await this.refresh()
    } catch { if (generation === this.state.generation) this.clear('unavailable') }
  }
  async refresh() {
    this.cloud = true; this.clear('loading')
    const generation = this.state.generation
    try {
      const response = await fetch('/api/account/me', { credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal: this.controller.signal })
      if (generation !== this.state.generation) return
      if (!response.ok) { this.clear(response.status === 401 ? 'expired' : 'unavailable'); return }
      const value = await response.json() as { user_id?: unknown; csrf_token?: unknown; email?: unknown }
      if (generation !== this.state.generation) return
      if (typeof value.user_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.user_id) || typeof value.csrf_token !== 'string' || !/^[a-f0-9]{64}$/.test(value.csrf_token)) throw new Error('Invalid account')
      this.csrf = value.csrf_token
      this.publish({ status: 'authenticated', userId: value.user_id, email: typeof value.email === 'string' ? value.email : null, generation })
    } catch { if (generation === this.state.generation) this.clear('unavailable') }
  }
  async request(input: string, init?: RequestInit): Promise<Response> {
    if (this.state.status === 'personal') return init === undefined ? fetch(input) : fetch(input, init)
    init ??= {}
    if (!this.cloud || this.state.status !== 'authenticated' || !this.csrf) throw new CloudAuthError(401, 'Cloud session unavailable')
    const url = new URL(input, location.href)
    if (url.origin !== location.origin || url.username || url.password) throw new CloudAuthError(403, 'Cross-origin cloud request refused')
    const owner = this.state.userId
    const claimed = url.searchParams.get('user_id')
    if (claimed !== null && claimed !== owner) throw new CloudAuthError(409, 'Identity changed')
    const verifyClaims = (value: unknown): void => {
      if (Array.isArray(value)) { value.forEach(verifyClaims); return }
      if (value && typeof value === 'object') {
        for (const [key, entry] of Object.entries(value)) {
          if (key === 'user_id' && entry !== owner) throw new CloudAuthError(409, 'Identity changed')
          if (entry && typeof entry === 'object') verifyClaims(entry)
        }
      }
    }
    if (typeof init.body === 'string') {
      try { verifyClaims(JSON.parse(init.body)) } catch (error) { if (error instanceof CloudAuthError) throw error }
    }
    const generation = this.state.generation
    const headers = new Headers(init.headers)
    if (!['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase())) headers.set('X-CSRF-Token', this.csrf)
    const response = await fetch(url.pathname + url.search, { ...init, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.any([this.controller.signal, ...(init.signal ? [init.signal] : [])]) })
    if (generation !== this.state.generation) throw new CloudAuthError(409, 'Identity changed')
    if (response.status === 401 || response.status === 403) {
      this.clear(response.status === 401 ? 'expired' : 'csrf-invalid')
      throw new CloudAuthError(response.status, 'Cloud authorization rejected')
    }
    if (response.status === 409 || response.status === 503) throw new CloudAuthError(response.status, response.status === 409 ? 'Request conflicts with saved state' : 'Cloud service unavailable')
    const privatePath = /^\/api\/(library|study-bank|review|ai|integration)(\/|$)/.test(url.pathname) || /^\/api\/topics\/[^/]+\/(preview|attempts)(\/|$)/.test(url.pathname)
    const responseOwner = response.headers.get('X-Authenticated-User')
    if (response.ok && ((privatePath && responseOwner !== owner) || (responseOwner !== null && responseOwner !== owner))) {
      this.clear('expired')
      throw new CloudAuthError(409, 'Authenticated account changed; confirm the session again')
    }
    const originalJson = response.json.bind(response)
    Object.defineProperty(response, 'json', { value: async () => {
      const value = await originalJson()
      if (generation !== this.state.generation) throw new CloudAuthError(409, 'Identity changed')
      return value
    } })
    return response
  }
  async logout() {
    const generation = this.state.generation
    let confirmed = false
    try {
      const response = await this.request('/api/account/logout', { method: 'POST' })
      if (!response.ok) throw new CloudAuthError(response.status, 'Logout not confirmed')
      confirmed = true
    } finally { if (generation === this.state.generation) this.clear(confirmed ? 'logged-out' : 'logout-unconfirmed') }
  }
  async revokeAll() {
    const generation = this.state.generation
    let confirmed = false
    try {
      const response = await this.request('/api/account/sessions/revoke-all', { method: 'POST' })
      if (!response.ok) throw new CloudAuthError(response.status, 'Sign-out everywhere not confirmed')
      confirmed = true
    } finally { if (generation === this.state.generation) this.clear(confirmed ? 'logged-out' : 'logout-unconfirmed') }
  }
  async rotate() {
    const response = await this.request('/api/account/session/rotate', { method: 'POST' })
    if (!response.ok) throw new CloudAuthError(response.status, 'Rotation not confirmed')
    await this.refresh()
  }
}

export type EmailChallenge = { challengeId: string; expiresIn: number; resendAfter: number }
export type LoginProviders = { email: boolean; wechat: boolean }

// Sessionless sign-in calls. The browser sends Origin on these POSTs and the
// server enforces it; no CSRF token exists until the session is issued.
async function loginPost(path: string, body: unknown): Promise<Response> {
  return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin', cache: 'no-store', redirect: 'error' })
}

export async function loginProviders(): Promise<LoginProviders> {
  const response = await fetch('/api/account/providers', { credentials: 'omit', cache: 'no-store', redirect: 'error' })
  if (!response.ok) return { email: false, wechat: false }
  const value = await response.json() as Partial<LoginProviders>
  return { email: value.email === true, wechat: value.wechat === true }
}

export async function startEmailLogin(email: string): Promise<EmailChallenge> {
  const response = await loginPost('/api/account/email/start', { email })
  if (response.status === 429) {
    const value = await response.json().catch(() => ({})) as { detail?: { retry_after?: unknown } }
    const retry = Number(value.detail?.retry_after ?? response.headers.get('Retry-After'))
    throw new CloudAuthError(429, `retry:${Number.isFinite(retry) && retry > 0 ? Math.ceil(retry) : 60}`)
  }
  if (!response.ok) throw new CloudAuthError(response.status, response.status === 422 ? 'email-invalid' : 'unavailable')
  const value = await response.json() as { challenge_id?: unknown; expires_in?: unknown; resend_after?: unknown }
  if (typeof value.challenge_id !== 'string' || !/^[0-9a-f-]{36}$/.test(value.challenge_id) || typeof value.expires_in !== 'number' || typeof value.resend_after !== 'number') throw new CloudAuthError(503, 'unavailable')
  return { challengeId: value.challenge_id, expiresIn: value.expires_in, resendAfter: value.resend_after }
}

export async function verifyEmailLogin(challengeId: string, code: string): Promise<void> {
  const response = await loginPost('/api/account/email/verify', { challenge_id: challengeId, code })
  if (!response.ok) throw new CloudAuthError(response.status, response.status === 400 || response.status === 422 ? 'code-invalid' : 'unavailable')
  // The session cookie is HttpOnly; confirm it through the normal account check.
  await cloudAuth.refresh()
  broadcastAuth('signed-in')
}

export const AUTH_CHANNEL = 'language-ai-auth-invalidation'
// Other tabs re-check the account on 'signed-in' and drop private views on 'invalidate'.
export function broadcastAuth(kind: 'invalidate' | 'signed-in') {
  if (typeof BroadcastChannel === 'undefined') return
  const channel = new BroadcastChannel(AUTH_CHANNEL)
  channel.postMessage(kind)
  channel.close()
}

async function detailCode(response: Response): Promise<string> {
  const value = await response.json().catch(() => ({})) as { detail?: { code?: unknown } }
  return typeof value.detail?.code === 'string' ? value.detail.code : 'unavailable'
}

function authorizationUrl(value: unknown): string {
  const url = typeof value === 'object' && value ? (value as { authorization_url?: unknown }).authorization_url : undefined
  if (typeof url !== 'string' || new URL(url).protocol !== 'https:') throw new CloudAuthError(503, 'unavailable')
  return url
}

export async function startWeChatLogin(): Promise<string> {
  const response = await loginPost('/api/account/wechat/start', {})
  if (!response.ok) throw new CloudAuthError(response.status, await detailCode(response))
  return authorizationUrl(await response.json())
}

export type OAuthCallback = { code: string; state: string } | { cancelled: true }

// WeChat returns to /?oauth=wechat&code=…&state=…; the code is removed from the
// address bar before anything else runs so it does not linger in history.
export function readOAuthCallback(): OAuthCallback | null {
  const params = new URLSearchParams(location.search ?? '')
  if (params.get('oauth') !== 'wechat') return null
  history.replaceState(null, '', location.pathname ?? '/')
  const code = params.get('code'), state = params.get('state')
  return code && state ? { code, state } : { cancelled: true }
}

let pendingCompletion: { state: string; result: Promise<'login' | 'link'> } | null = null
// Completes each callback once even if React mounts the provider twice.
export function completeWeChat(callback: { code: string; state: string }): Promise<'login' | 'link'> {
  if (pendingCompletion?.state === callback.state) return pendingCompletion.result
  const result = (async () => {
    const response = await loginPost('/api/account/wechat/complete', callback)
    if (!response.ok) throw new CloudAuthError(response.status, await detailCode(response))
    const value = await response.json() as { purpose?: unknown }
    if (value.purpose === 'link') return 'link' as const
    await cloudAuth.refresh()
    broadcastAuth('signed-in')
    return 'login' as const
  })()
  pendingCompletion = { state: callback.state, result }
  return result
}

export type LinkedIdentity = { provider: 'email' | 'wechat'; label: string; linkedAt: string }
export type AccountIdentities = { identities: LinkedIdentity[]; reauthenticatedFor: number; wechatAvailable: boolean }

async function accountCall(path: string, method: 'GET' | 'POST', body?: unknown): Promise<Response> {
  const response = await cloudAuth.request(path, body === undefined ? { method } : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw new CloudAuthError(response.status, response.status === 429 ? 'throttled' : await detailCode(response))
  return response
}

export async function accountIdentities(): Promise<AccountIdentities> {
  const value = await (await accountCall('/api/account/identities', 'GET')).json() as { identities?: unknown; reauthenticated_for?: unknown; wechat_available?: unknown }
  const identities = Array.isArray(value.identities) ? value.identities.flatMap((item: { provider?: unknown; label?: unknown; linked_at?: unknown }): LinkedIdentity[] =>
    (item.provider === 'email' || item.provider === 'wechat') && typeof item.label === 'string' && typeof item.linked_at === 'string'
      ? [{ provider: item.provider, label: item.label, linkedAt: item.linked_at }] : []) : []
  return { identities, reauthenticatedFor: typeof value.reauthenticated_for === 'number' ? value.reauthenticated_for : 0, wechatAvailable: value.wechat_available === true }
}

export async function startReauth(): Promise<EmailChallenge & { email: string }> {
  const value = await (await accountCall('/api/account/reauth/email/start', 'POST')).json() as { challenge_id?: unknown; expires_in?: unknown; resend_after?: unknown; email?: unknown }
  if (typeof value.challenge_id !== 'string' || typeof value.email !== 'string' || typeof value.expires_in !== 'number' || typeof value.resend_after !== 'number') throw new CloudAuthError(503, 'unavailable')
  return { challengeId: value.challenge_id, expiresIn: value.expires_in, resendAfter: value.resend_after, email: value.email }
}

export async function verifyReauth(challengeId: string, code: string): Promise<void> {
  await accountCall('/api/account/reauth/email/verify', 'POST', { challenge_id: challengeId, code })
}

export async function startWeChatLink(): Promise<string> {
  return authorizationUrl(await (await accountCall('/api/account/wechat/link/start', 'POST', { confirm: 'link-wechat' })).json())
}

export async function unlinkWeChat(): Promise<void> {
  await accountCall('/api/account/identities/unlink', 'POST', { provider: 'wechat', confirm: 'unlink-wechat' })
}

export const cloudAuth = new CloudAuthClient()
export const apiFetch = (input: string, init?: RequestInit) => cloudAuth.request(input, init)

export type EmailClaimResult = { result: 'added' | 'changed'; notice: 'sent' | 'failed' | 'unavailable' }

// Prove control of a new address for the signed-in account (needs a fresh email proof).
export async function startEmailClaim(email: string, replaces: string | null): Promise<EmailChallenge> {
  const value = await (await accountCall('/api/account/email/claim/start', 'POST', { email, replaces, confirm: replaces ? 'change-email' : 'add-email' })).json() as { challenge_id?: unknown; expires_in?: unknown; resend_after?: unknown }
  if (typeof value.challenge_id !== 'string' || typeof value.expires_in !== 'number' || typeof value.resend_after !== 'number') throw new CloudAuthError(503, 'unavailable')
  return { challengeId: value.challenge_id, expiresIn: value.expires_in, resendAfter: value.resend_after }
}

export async function verifyEmailClaim(challengeId: string, code: string): Promise<EmailClaimResult> {
  const value = await (await accountCall('/api/account/email/claim/verify', 'POST', { challenge_id: challengeId, code })).json() as { result?: unknown; notice?: unknown }
  const result = value.result === 'changed' ? 'changed' : 'added'
  const notice = value.notice === 'sent' || value.notice === 'unavailable' ? value.notice : 'failed'
  return { result, notice }
}
