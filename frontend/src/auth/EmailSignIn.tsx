import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { CloudAuthError, loginProviders, startEmailLogin, verifyEmailLogin } from './client'
import type { EmailChallenge } from './client'

type Step = { kind: 'checking' } | { kind: 'not-configured' } | { kind: 'email' } | { kind: 'code'; email: string; challenge: EmailChallenge }

function wait(seconds: number): string {
  if (seconds < 120) return `${seconds} seconds`
  if (seconds < 7200) return `${Math.ceil(seconds / 60)} minutes`
  return `about ${Math.ceil(seconds / 3600)} hours`
}

function message(error: unknown): string {
  if (error instanceof CloudAuthError) {
    if (error.status === 429) return `Too many codes requested. Try again in ${wait(Number(error.message.replace('retry:', '')))}.`
    if (error.message === 'email-invalid') return 'Enter a valid email address.'
    if (error.message === 'code-invalid') return 'That code is invalid or has expired. Check the latest email or request a new code.'
  }
  return 'Sign-in is temporarily unavailable. Try again shortly.'
}

export function EmailSignIn() {
  const [step, setStep] = useState<Step>({ kind: 'checking' })
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [resendIn, setResendIn] = useState(0)

  useEffect(() => {
    let active = true
    void loginProviders().then((providers) => { if (active) setStep(providers.email ? { kind: 'email' } : { kind: 'not-configured' }) })
      .catch(() => { if (active) setStep({ kind: 'not-configured' }) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (resendIn <= 0) return
    const timer = window.setTimeout(() => setResendIn((value) => value - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [resendIn])

  async function requestCode(address: string) {
    setBusy(true); setError(null)
    try {
      const challenge = await startEmailLogin(address)
      setStep({ kind: 'code', email: address, challenge }); setCode(''); setResendIn(challenge.resendAfter)
    } catch (caught) { setError(message(caught)) } finally { setBusy(false) }
  }
  async function submitEmail(event: FormEvent) { event.preventDefault(); await requestCode(email.trim()) }
  async function submitCode(event: FormEvent) {
    event.preventDefault()
    if (step.kind !== 'code') return
    setBusy(true); setError(null)
    try { await verifyEmailLogin(step.challenge.challengeId, code) } catch (caught) { setError(message(caught)) } finally { setBusy(false) }
  }

  if (step.kind === 'checking') return <p role="status">Checking sign-in options…</p>
  if (step.kind === 'not-configured') return <p>Email sign-in is not configured on this server yet. Ask the operator to enable a verified email provider.</p>
  return <section aria-labelledby="sign-in-heading">
    <h2 id="sign-in-heading">Sign in with email</h2>
    {step.kind === 'email' ? <form onSubmit={(event) => void submitEmail(event)}>
      <label htmlFor="sign-in-email">Email address</label>
      <input id="sign-in-email" type="email" autoComplete="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} />
      <button type="submit" disabled={busy}>Send sign-in code</button>
    </form> : <form onSubmit={(event) => void submitCode(event)}>
      <p>We sent a 6-digit code to <strong>{step.email}</strong>. It expires in {Math.round(step.challenge.expiresIn / 60)} minutes.</p>
      <label htmlFor="sign-in-code">Sign-in code</label>
      <input id="sign-in-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))} />
      <button type="submit" disabled={busy || code.length !== 6}>Sign in</button>
      <button type="button" disabled={busy || resendIn > 0} onClick={() => void requestCode(step.email)}>{resendIn > 0 ? `Resend code (${resendIn}s)` : 'Resend code'}</button>
      <button type="button" disabled={busy} onClick={() => { setStep({ kind: 'email' }); setError(null) }}>Use a different email</button>
    </form>}
    {error && <p role="alert">{error}</p>}
  </section>
}
