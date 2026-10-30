export type AuthState =
  | { status: 'loading' | 'unavailable' | 'expired' | 'csrf-invalid' | 'logged-out' | 'logout-unconfirmed'; generation: number }
  | { status: 'personal'; generation: number }
  | { status: 'authenticated'; userId: string; generation: number }

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
      const value = await response.json() as { user_id?: unknown; csrf_token?: unknown }
      if (generation !== this.state.generation) return
      if (typeof value.user_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.user_id) || typeof value.csrf_token !== 'string' || !/^[a-f0-9]{64}$/.test(value.csrf_token)) throw new Error('Invalid account')
      this.csrf = value.csrf_token
      this.publish({ status: 'authenticated', userId: value.user_id, generation })
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
  async rotate() {
    const response = await this.request('/api/account/session/rotate', { method: 'POST' })
    if (!response.ok) throw new CloudAuthError(response.status, 'Rotation not confirmed')
    await this.refresh()
  }
}

export const cloudAuth = new CloudAuthClient()
export const apiFetch = (input: string, init?: RequestInit) => cloudAuth.request(input, init)
