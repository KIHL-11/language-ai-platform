import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { CloudAuthError, accountIdentities, startReauth, startWeChatLink, unlinkWeChat, verifyReauth } from './client'
import type { AccountIdentities } from './client'
import { codeComplete, normalizeCode } from './codes'

function explain(error: unknown): string {
  if (error instanceof CloudAuthError) {
    if (error.status === 428) return 'Confirm your email address first, then try again.'
    if (error.status === 409) return 'That WeChat account is already linked to an account, or this account already has WeChat linked.'
    if (error.status === 429) return 'Too many codes requested. Wait a minute and try again.'
    if (error.message === 'email_code_invalid') return 'That code is invalid or has expired.'
    if (error.message === 'last_login_method') return 'You cannot remove your only sign-in method.'
  }
  return 'Account settings are temporarily unavailable. Try again shortly.'
}

// A sent re-check code survives the workspace remounting on a same-account
// refresh; it is only an id and an address, never the code.
const pendingReauth = new Map<string, { id: string; email: string }>()

// Linking and unlinking need a fresh email proof (valid 10 minutes) and an
// explicit confirmation; the server enforces both.
export function AccountSecurity({ userId, notice }: { userId: string; notice?: string | null }) {
  const [account, setAccount] = useState<AccountIdentities | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [challenge, setPending] = useState<{ id: string; email: string } | null>(() => pendingReauth.get(userId) ?? null)
  const setChallenge = (value: { id: string; email: string } | null) => {
    if (value) pendingReauth.set(userId, value); else pendingReauth.delete(userId)
    setPending(value)
  }
  const [code, setCode] = useState('')
  const [confirmLink, setConfirmLink] = useState(false)
  const [confirmUnlink, setConfirmUnlink] = useState(false)

  async function load() {
    try { setAccount(await accountIdentities()) } catch (caught) { setError(explain(caught)) }
  }
  useEffect(() => {
    let active = true
    accountIdentities().then((value) => { if (active) setAccount(value) }, (caught: unknown) => { if (active) setError(explain(caught)) })
    return () => { active = false }
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await action() } catch (caught) { setError(explain(caught)) } finally { setBusy(false) }
  }
  const sendCode = () => run(async () => { const started = await startReauth(); setChallenge({ id: started.challengeId, email: started.email }); setCode('') })
  const submitCode = (event: FormEvent) => {
    event.preventDefault()
    if (!challenge) return
    void run(async () => { await verifyReauth(challenge.id, code.replace(/-/g, '')); setChallenge(null); await load() })
  }
  const link = () => run(async () => { location.assign(await startWeChatLink()) })
  const unlink = () => run(async () => { await unlinkWeChat(); setConfirmUnlink(false); await load() })

  if (!account) return <main className="p-8"><h2>Sign-in methods</h2>{error ? <p role="alert">{error}</p> : <p role="status">Loading sign-in methods…</p>}</main>
  const hasWeChat = account.identities.some((item) => item.provider === 'wechat')
  const fresh = account.reauthenticatedFor > 0
  return <main className="p-8">
    <h2>Sign-in methods</h2>
    {notice && <p role="status">{notice}</p>}
    <ul>{account.identities.map((item) => <li key={item.provider}>{item.provider === 'email' ? `Email: ${item.label}` : 'WeChat'} (linked {new Date(item.linkedAt).toLocaleDateString()})</li>)}</ul>
    {fresh ? <p>Email confirmed. You can change sign-in methods for about {Math.max(1, Math.round(account.reauthenticatedFor / 60))} more minutes.</p>
      : challenge ? <form onSubmit={submitCode}>
        <p>We sent a code to <strong>{challenge.email}</strong>.</p>
        <label htmlFor="reauth-code">Confirmation code</label>
        <input id="reauth-code" autoComplete="one-time-code" maxLength={14} required value={code} onChange={(event) => setCode(normalizeCode(event.target.value))} />
        <button type="submit" disabled={busy || !codeComplete(code)}>Confirm email</button>
      </form>
      : <p>To link or remove a sign-in method, first confirm your email address. <button type="button" disabled={busy} onClick={() => void sendCode()}>Send confirmation code</button></p>}
    {account.wechatAvailable && !hasWeChat && <section aria-labelledby="link-wechat-heading">
      <h3 id="link-wechat-heading">Link WeChat</h3>
      <label><input type="checkbox" checked={confirmLink} onChange={(event) => setConfirmLink(event.target.checked)} /> I want the WeChat account I approve next to be able to sign in to this Language AI account.</label>
      <button type="button" disabled={busy || !fresh || !confirmLink} onClick={() => void link()}>Continue to WeChat</button>
    </section>}
    {hasWeChat && <section aria-labelledby="unlink-wechat-heading">
      <h3 id="unlink-wechat-heading">Remove WeChat</h3>
      <label><input type="checkbox" checked={confirmUnlink} onChange={(event) => setConfirmUnlink(event.target.checked)} /> Remove WeChat sign-in and sign out my other browsers.</label>
      <button type="button" disabled={busy || !fresh || !confirmUnlink} onClick={() => void unlink()}>Remove WeChat</button>
    </section>}
    {error && <p role="alert">{error}</p>}
  </main>
}
