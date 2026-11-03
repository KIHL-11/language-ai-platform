import { useState } from 'react'
import type { FormEvent } from 'react'
import { CloudAuthError, startEmailClaim, verifyEmailClaim } from './client'
import { codeComplete, normalizeCode } from './codes'

function explain(error: unknown): string {
  if (error instanceof CloudAuthError) {
    if (error.status === 428) return 'Your email confirmation expired. Confirm your email again, then retry.'
    if (error.status === 409) return 'That address already belongs to another Language AI account. Nothing was changed.'
    if (error.status === 429) return 'Too many codes requested for that address. Wait a minute and try again.'
    if (error.message === 'email_code_invalid') return 'That code is invalid or has expired.'
    if (error.message === 'email_already_yours') return 'That address is already on your account.'
    if (error.message === 'email_limit') return 'An account can have at most two email addresses.'
    if (error.message === 'email_invalid') return 'Enter a valid email address.'
  }
  return 'Email settings are temporarily unavailable. Try again shortly.'
}

type Step = { kind: 'idle' } | { kind: 'enter'; replaces: string | null } | { kind: 'code'; replaces: string | null; email: string; challengeId: string }

// Add a second address or replace one. The server requires a fresh email proof,
// the explicit confirmation and a code sent to the new address.
export function EmailAddresses({ emails, fresh, onChanged }: { emails: string[]; fresh: boolean; onChanged: () => Promise<void> }) {
  const [step, setStep] = useState<Step>({ kind: 'idle' })
  const [address, setAddress] = useState('')
  const [code, setCode] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<{ text: string; warning: string | null } | null>(null)

  function begin(replaces: string | null) { setStep({ kind: 'enter', replaces }); setAddress(''); setConfirmed(false); setError(null); setOutcome(null) }
  async function sendCode(event: FormEvent) {
    event.preventDefault()
    if (step.kind !== 'enter') return
    setBusy(true); setError(null)
    try {
      const started = await startEmailClaim(address.trim(), step.replaces)
      setStep({ kind: 'code', replaces: step.replaces, email: address.trim(), challengeId: started.challengeId }); setCode('')
    } catch (caught) { setError(explain(caught)) } finally { setBusy(false) }
  }
  async function submitCode(event: FormEvent) {
    event.preventDefault()
    if (step.kind !== 'code') return
    setBusy(true); setError(null)
    try {
      const done = await verifyEmailClaim(step.challengeId, code.replace(/-/g, ''))
      const told = done.result === 'changed' ? step.replaces : emails[0]
      setOutcome({
        text: done.result === 'changed' ? `Your address is now ${step.email}. Other browsers were signed out.` : `${step.email} can now sign in to your account.`,
        // A failed notice is not "all done": say so plainly.
        warning: done.notice === 'sent' ? null : `We could not send a security notice to ${told}. If you did not expect this change, sign out everywhere and review your sign-in methods.`,
      })
      setStep({ kind: 'idle' })
      await onChanged()
    } catch (caught) { setError(explain(caught)) } finally { setBusy(false) }
  }

  return <section aria-labelledby="email-addresses-heading">
    <h3 id="email-addresses-heading">Email addresses</h3>
    <ul>{emails.map((email, index) => <li key={email}>{email}{index === 0 ? ' (primary)' : ''} <button type="button" disabled={busy || !fresh} onClick={() => begin(email)}>Change {email}</button></li>)}</ul>
    {emails.length < 2 && <button type="button" disabled={busy || !fresh} onClick={() => begin(null)}>Add an email address</button>}
    {step.kind === 'enter' && <form onSubmit={(event) => void sendCode(event)}>
      <label htmlFor="claim-email">{step.replaces ? `New address to replace ${step.replaces}` : 'Additional email address'}</label>
      <input id="claim-email" type="email" autoComplete="email" required maxLength={254} value={address} onChange={(event) => setAddress(event.target.value)} />
      <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> {step.replaces ? `Replace ${step.replaces} with this address and sign out my other browsers.` : 'Let this address sign in to my Language AI account.'}</label>
      <button type="submit" disabled={busy || !confirmed}>Send code to new address</button>
      <button type="button" disabled={busy} onClick={() => setStep({ kind: 'idle' })}>Cancel</button>
    </form>}
    {step.kind === 'code' && <form onSubmit={(event) => void submitCode(event)}>
      <p>We sent a code to <strong>{step.email}</strong>.</p>
      <label htmlFor="claim-code">Code from the new address</label>
      <input id="claim-code" autoComplete="one-time-code" maxLength={14} required value={code} onChange={(event) => setCode(normalizeCode(event.target.value))} />
      <button type="submit" disabled={busy || !codeComplete(code)}>Confirm new address</button>
      <button type="button" disabled={busy} onClick={() => setStep({ kind: 'idle' })}>Cancel</button>
    </form>}
    {outcome && <p role="status">{outcome.text}</p>}
    {outcome?.warning && <p role="alert">{outcome.warning}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
