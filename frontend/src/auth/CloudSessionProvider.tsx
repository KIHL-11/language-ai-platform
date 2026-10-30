import { useEffect, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import { API_BASE_URL } from '../api/apiBase'
import { cloudAuth } from './client'
import StudyBankPage from '../pages/StudyBankPage'
import DailyReviewPage from '../pages/DailyReviewPage'
import TopicSpeakingCurriculum from '../speaking/TopicSpeakingCurriculum'
import { getLibraryItem, listLibraryItems } from '../api/library'
import type { LibraryItem, LibraryItemSummary } from '../types/library'

export function CloudSessionProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(cloudAuth.subscribe, cloudAuth.getSnapshot)
  const [bootstrapped, setBootstrapped] = useState(false)
  useEffect(() => {
    let active = true
    void cloudAuth.bootstrap(API_BASE_URL).then(() => { if (active) setBootstrapped(true) })
    const refresh = () => { if (cloudAuth.getSnapshot().status === 'authenticated') void cloudAuth.refresh() }
    window.addEventListener('focus', refresh)
    const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('language-ai-auth-invalidation')
    if (channel) channel.onmessage = () => cloudAuth.invalidate()
    return () => { active = false; window.removeEventListener('focus', refresh); channel?.close() }
  }, [])
  if (!bootstrapped) return <main><p role="status">Checking server deployment…</p></main>
  if (state.status === 'personal') return children
  if (state.status === 'authenticated') return <CloudWorkspace key={`${state.userId}:${state.generation}`} userId={state.userId} />
  return <main className="mx-auto max-w-3xl p-8"><h1>Language AI — Cloud session</h1><p role="status">{state.status === 'loading' ? 'Checking session…' : state.status === 'csrf-invalid' ? 'Authorization or CSRF validation failed. Refresh your session before continuing.' : state.status === 'expired' ? 'Your session is missing or expired.' : state.status === 'logout-unconfirmed' ? 'Local view cleared, but server logout was not confirmed.' : state.status === 'logged-out' ? 'Signed out of this browser view.' : 'Cloud authentication is unavailable.'}</p><p>Public login is unavailable until a verified provider is configured.</p>{state.status !== 'loading' && <button type="button" onClick={() => void cloudAuth.bootstrap(API_BASE_URL)}>Check session again</button>}</main>
}

function CloudWorkspace({ userId }: { userId: string }) {
  const [view, setView] = useState<'library' | 'study' | 'review' | 'topics'>('study')
  async function logout() {
    try { await cloudAuth.logout() }
    catch { /* The auth gate displays an unconfirmed logout state. */ }
    finally {
      if (typeof BroadcastChannel !== 'undefined') { const channel = new BroadcastChannel('language-ai-auth-invalidation'); channel.postMessage('invalidate'); channel.close() }
    }
  }
  return <div><header className="p-5"><h1>Language AI — Cloud</h1><nav aria-label="Cloud learning"><button onClick={() => setView('library')}>My Library</button><button onClick={() => setView('study')}>Words & Chunks</button><button onClick={() => setView('review')}>Daily Review</button><button onClick={() => setView('topics')}>Topic Speaking</button><button onClick={() => void logout()}>Sign out</button></nav></header>{view === 'library' ? <CloudLibrary /> : view === 'study' ? <StudyBankPage /> : view === 'review' ? <DailyReviewPage /> : <TopicSpeakingCurriculum learnerId={userId} onBack={() => setView('study')} />}</div>
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
