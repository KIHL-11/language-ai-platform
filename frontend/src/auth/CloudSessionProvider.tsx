import { useEffect, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import { API_BASE_URL } from '../api/apiBase'
import { AUTH_CHANNEL, CloudAuthError, broadcastAuth, cloudAuth, completeWeChat, readOAuthCallback } from './client'
import type { OAuthCallback } from './client'
import { AccountSecurity } from './AccountSecurity'
import { EmailSignIn } from './EmailSignIn'
import StudyBankPage from '../pages/StudyBankPage'
import DailyReviewPage from '../pages/DailyReviewPage'
import TopicSpeakingCurriculum from '../speaking/TopicSpeakingCurriculum'
import { getLibraryItem, listLibraryItems } from '../api/library'
import type { LibraryItem, LibraryItemSummary } from '../types/library'

type View = 'library' | 'study' | 'review' | 'topics' | 'account'
type WeChatResult = { purpose: 'login' | 'link' | 'error'; message: string }

function weChatFailure(error: unknown): string {
  if (error instanceof CloudAuthError) {
    if (error.message === 'wechat_not_linked') return 'This WeChat account is not linked yet. Sign in with email, then link WeChat in Sign-in methods.'
    if (error.message === 'identity_conflict' || error.message === 'provider_already_linked') return 'That WeChat account is already linked to a different Language AI account. Nothing was changed.'
    if (error.message === 'reauthentication_required') return 'Your email confirmation expired before WeChat finished. Confirm your email and try again.'
  }
  return 'WeChat sign-in could not be confirmed. Start again from this page.'
}

async function finishWeChat(callback: OAuthCallback): Promise<WeChatResult> {
  if ('cancelled' in callback) return { purpose: 'error', message: 'WeChat sign-in was cancelled.' }
  try {
    const purpose = await completeWeChat(callback)
    return { purpose, message: purpose === 'link' ? 'WeChat is now linked to your account.' : 'Signed in with WeChat.' }
  } catch (error) { return { purpose: 'error', message: weChatFailure(error) } }
}

const callbackOnLoad = typeof location === 'undefined' ? null : readOAuthCallback()

export function CloudSessionProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(cloudAuth.subscribe, cloudAuth.getSnapshot)
  const [bootstrapped, setBootstrapped] = useState(false)
  const [weChat, setWeChat] = useState<WeChatResult | null>(null)
  // Lives above the keyed workspace so a same-account refresh (e.g. on focus,
  // while the learner fetches a code from their mail tab) keeps the view.
  const [viewFor, setViewFor] = useState<{ userId: string; view: View } | null>(null)
  useEffect(() => {
    let active = true
    void cloudAuth.bootstrap(API_BASE_URL).then(async () => {
      const callback = callbackOnLoad ?? readOAuthCallback()
      if (callback && cloudAuth.getSnapshot().status !== 'personal' && cloudAuth.getSnapshot().status !== 'unavailable') {
        const result = await finishWeChat(callback)
        if (active) setWeChat(result)
      }
      if (active) setBootstrapped(true)
    })
    const refresh = () => { if (cloudAuth.getSnapshot().status === 'authenticated') void cloudAuth.refresh() }
    window.addEventListener('focus', refresh)
    const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(AUTH_CHANNEL)
    if (channel) channel.onmessage = (event: MessageEvent) => { if (event.data === 'signed-in') void cloudAuth.refresh(); else cloudAuth.receive(event.data) }
    return () => { active = false; window.removeEventListener('focus', refresh); channel?.close() }
  }, [])
  if (!bootstrapped) return <main><p role="status">Checking server deployment…</p></main>
  if (state.status === 'personal') return children
  if (state.status === 'logout-unconfirmed-elsewhere') return <LogoutUnconfirmedElsewhere />
  if (state.status === 'authenticated') {
    const view = viewFor?.userId === state.userId ? viewFor.view : weChat && weChat.purpose !== 'login' ? 'account' : 'study'
    return <CloudWorkspace key={`${state.userId}:${state.generation}`} userId={state.userId} email={state.email} weChat={weChat} view={view} setView={(next) => setViewFor({ userId: state.userId, view: next })} />
  }
  return <main className="mx-auto max-w-3xl p-8"><h1>Language AI — Cloud session</h1>{weChat && <p role="alert">{weChat.message}</p>}<p role="status">{state.status === 'loading' ? 'Checking session…' : state.status === 'csrf-invalid' ? 'Authorization or CSRF validation failed. Refresh your session before continuing.' : state.status === 'expired' ? 'Your session is missing or expired.' : state.status === 'logout-unconfirmed' ? 'Local view cleared, but server logout was not confirmed.' : state.status === 'logged-out' ? 'Signed out of this browser view.' : 'Cloud authentication is unavailable.'}</p>{state.status !== 'loading' && state.status !== 'unavailable' && <EmailSignIn />}{state.status !== 'loading' && <button type="button" onClick={() => void cloudAuth.bootstrap(API_BASE_URL)}>Check session again</button>}</main>
}

function CloudWorkspace({ userId, email, weChat, view, setView }: { userId: string; email: string | null; weChat: WeChatResult | null; view: View; setView: (view: View) => void }) {
  async function logout(everywhere = false) {
    let confirmed = false
    try { await (everywhere ? cloudAuth.revokeAll() : cloudAuth.logout()); confirmed = true }
    catch { /* The auth gate displays an unconfirmed logout state. */ }
    finally { broadcastAuth(confirmed ? 'logout-confirmed' : 'logout-unconfirmed') }
  }
  return <div><header className="p-5"><h1>Language AI — Cloud</h1>{email && <p>Signed in as {email}</p>}<nav aria-label="Cloud learning"><button onClick={() => setView('library')}>My Library</button><button onClick={() => setView('study')}>Words & Chunks</button><button onClick={() => setView('review')}>Daily Review</button><button onClick={() => setView('topics')}>Topic Speaking</button><button onClick={() => setView('account')}>Sign-in methods</button><button onClick={() => void logout()}>Sign out</button><button onClick={() => void logout(true)}>Sign out everywhere</button></nav></header>{view === 'library' ? <CloudLibrary /> : view === 'study' ? <StudyBankPage /> : view === 'review' ? <DailyReviewPage /> : view === 'account' ? <AccountSecurity userId={userId} notice={weChat?.message} /> : <TopicSpeakingCurriculum learnerId={userId} onBack={() => setView('study')} />}</div>
}

function CloudLibrary() {
  const [items, setItems] = useState<LibraryItemSummary[]>([])
  const [selected, setSelected] = useState<LibraryItem | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let active = true
    void listLibraryItems().then((values) => { if (active) setItems(values) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [])
  return <main className="p-8"><h2>My Library</h2><p>Cloud Library read-only preview. Local training and media workflows remain unavailable.</p>{error && <p role="alert">Library unavailable.</p>}<ul>{items.map((item) => <li key={item.id}><button onClick={() => void getLibraryItem(item.id).then(setSelected).catch(() => setError(true))}>{item.title}</button></li>)}</ul>{selected && <article><h3>{selected.lesson.title}</h3>{selected.lesson.sentences.map((sentence) => <p key={sentence.id}>{sentence.text}</p>)}</article>}</main>
}

// Another tab tried to sign out and the server did not confirm it. This tab's
// session may still be live, so its workspace (and every request) stays closed
// until the learner rechecks the session or retries the sign-out.
function LogoutUnconfirmedElsewhere() {
  const [busy, setBusy] = useState(false)
  async function check() {
    setBusy(true)
    try { await cloudAuth.checkSession() } finally { setBusy(false) }
  }
  async function retry() {
    setBusy(true)
    try { await cloudAuth.retryLogout() }
    catch { /* The gate shows the outcome. */ }
    finally {
      // Broadcast only an outcome this retry produced; a retry superseded by
      // another tab's message or a new sign-in announces nothing.
      const status = cloudAuth.getSnapshot().status
      if (status === 'logged-out') broadcastAuth('logout-confirmed')
      else if (status === 'logout-unconfirmed') broadcastAuth('logout-unconfirmed')
      setBusy(false)
    }
  }
  return <main className="mx-auto max-w-3xl p-8"><h1>Language AI — Cloud session</h1><p role="status">另一个标签页尝试退出，但服务器未确认。当前会话可能仍然有效。</p><button type="button" disabled={busy} onClick={() => void check()}>检查会话</button><button type="button" disabled={busy} onClick={() => void retry()}>重试退出</button></main>
}
