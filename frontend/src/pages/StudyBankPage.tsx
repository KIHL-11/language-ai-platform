import { useEffect, useState } from 'react'

import {
  deleteStudyItem,
  listStudyItems,
  updateStudyItem,
} from '../api/studyBank'
import { getStudyPracticeEvidence } from '../api/integration'
import { getSessionLearnerId } from '../auth/identity'
import type { SourceLanguage } from '../types/lesson'
import type { StudyItem, StudyKind } from '../types/studyBank'
import type { StudyPracticeEvidence } from '../types/integration'
import AiChunkOrganizer from '../components/AiChunkOrganizer'
import { cloudAuth } from '../auth/client'


function reviewLabel(item: StudyItem): string {
  if (item.review.review_count === 0) return 'New'
  const due = new Date(item.review.due_at)
  if (due.getTime() <= Date.now()) return 'Due now'
  return `Next review: ${due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
}


export default function StudyBankPage() {
  const [learnerId] = useState(getSessionLearnerId)
  const [kind, setKind] = useState<StudyKind>('word')
  const [language, setLanguage] = useState<SourceLanguage | 'all'>('all')
  const [active, setActive] = useState(true)
  const [search, setSearch] = useState('')
  const [items, setItems] = useState<StudyItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<Record<string, StudyPracticeEvidence>>({})

  useEffect(() => {
    let current = true
    void listStudyItems({
      userId: learnerId,
      kind,
      active,
      ...(language === 'all' ? {} : { language }),
      search,
    }).then((values) => {
      if (current) {
        setItems(values)
        setError(null)
        if (cloudAuth.getSnapshot().status === 'personal') void Promise.all(values.map(async (item) => [item.id, await getStudyPracticeEvidence(learnerId, item.id)] as const))
          .then((entries) => { if (current) setEvidence(Object.fromEntries(entries)) })
          .catch(() => { if (current) setEvidence({}) })
      }
    }).catch(() => {
      if (current) setError('Words & Chunks is unavailable. Please try again.')
    }).finally(() => {
      if (current) setLoading(false)
    })
    return () => { current = false }
  }, [active, kind, language, learnerId, search])

  async function setItemActive(item: StudyItem, value: boolean) {
    try {
      await updateStudyItem(learnerId, item.id, {
        active: value,
        ...(item.revision ? { expected_revision: item.revision } : {}),
      })
      setItems((current) => current.filter((candidate) => candidate.id !== item.id))
    } catch {
      setError('The Study Bank item could not be updated.')
    }
  }

  async function remove(item: StudyItem) {
    if (!window.confirm('Permanently delete this Study Bank item?\nPractice history will be preserved.')) return
    try {
      await deleteStudyItem(learnerId, item.id, item.revision)
      setItems((current) => current.filter((candidate) => candidate.id !== item.id))
    } catch {
      setError('The Study Bank item could not be deleted.')
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
      <h1 className="text-3xl font-semibold">Words & Chunks</h1>
      <p className="mt-2 text-sm text-[#66726e]">Your explicitly curated long-term study material.</p>
      <AiChunkOrganizer learnerId={learnerId} />
      <div className="mt-6 flex flex-wrap gap-2">
        {(['word', 'chunk'] as const).map((value) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => setKind(value)} className="h-10 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white">{value === 'word' ? 'Words' : 'Chunks'}</button>)}
        {(['all', 'en', 'de'] as const).map((value) => <button key={value} type="button" aria-pressed={language === value} onClick={() => setLanguage(value)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">{value === 'all' ? 'All' : value === 'en' ? 'English' : 'German'}</button>)}
        <button type="button" aria-pressed={active} onClick={() => setActive(true)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">Active</button>
        <button type="button" aria-pressed={!active} onClick={() => setActive(false)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">Archived</button>
        <label className="sr-only" htmlFor="study-search">Search Words & Chunks</label>
        <input id="study-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search Words & Chunks" className="h-10 min-w-64 border border-[#aebcb6] px-3" />
      </div>
      {loading && <p role="status" className="mt-8">Loading Words & Chunks…</p>}
      {error && <p role="alert" className="mt-8 text-[#8f1d14]">{error}</p>}
      {!loading && !error && items.length === 0 && <p className="mt-8 border-y border-[#ccd6d1] bg-white p-6">No {active ? 'active' : 'archived'} {kind === 'word' ? 'words' : 'chunks'} yet.</p>}
      <ul className="mt-8 space-y-4">
        {items.map((item) => (
          <li key={item.id} className="border-y border-[#ccd6d1] bg-white p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold">{item.text}</h2>
                <p className="mt-1">{item.meaning || 'Meaning unavailable'}</p>
                <p className="mt-2 text-sm text-[#66726e]">{item.source_language === 'en' ? 'English' : 'German'} · {item.provenance.length} source{item.provenance.length === 1 ? '' : 's'} · {item.active ? 'Active' : 'Archived'} · {reviewLabel(item)} · Saved {new Date(item.created_at).toLocaleDateString()}</p>
                {evidence[item.id] && <p className="mt-1 text-sm text-[#66726e]">Speaking: {evidence[item.id].recent_used_session_count ? 'Used recently' : 'Not used recently'} · Shadowing: {evidence[item.id].stable_source_sentence_count ? 'Stable source sync' : evidence[item.id].preferred_shadowing_source ? 'Source practice available' : 'Unavailable'}{item.kind === 'chunk' ? ` · ${evidence[item.id].guided_practice_count} Guided attempts` : ''}</p>}
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => void setItemActive(item, !item.active)} className="h-9 border border-[#176b5b] px-3">{item.active ? 'Archive' : 'Restore'}</button>
                <button type="button" onClick={() => void remove(item)} className="h-9 border border-[#b42318] px-3 text-[#b42318]">Delete</button>
              </div>
            </div>
            <details className="mt-4">
              <summary className="cursor-pointer font-medium">Sources</summary>
              <ul className="mt-3 space-y-3">
                {item.provenance.map((source) => source.source_type === 'media' ? <li key={`media:${source.library_item_id}:${source.sentence_id}:${source.start}`} className="border-l-2 border-[#ccd6d1] pl-3">
                  <p className="font-medium">{source.media_title}</p>
                  <p className="text-xs text-[#66726e]">{source.start.toFixed(1)}–{source.end.toFixed(1)}s</p>
                  <p className="mt-1 text-sm">{source.sentence_text}</p>
                  <p className="mt-1 text-sm text-[#66726e]">{source.sentence_translation}</p>
                </li> : source.source_type === 'speaking' ? <li key={`speaking:${source.speaking_session_id}:${source.suggestion_index}`} className="border-l-2 border-[#ccd6d1] pl-3">
                  <p className="font-medium">From Speaking practice · {source.provider === 'gemini-live' ? 'Gemini Live' : 'OpenAI Realtime'}</p>
                  {source.media_title_snapshot && <p className="text-xs text-[#66726e]">Discussed: {source.media_title_snapshot}</p>}
                  <p className="mt-1 text-sm">Suggested in turn {source.originating_turn}</p>
                  {source.evidence_text && <p className="mt-1 text-sm text-[#66726e]">“{source.evidence_text}”</p>}
                </li> : <li key={`ai-text:${source.source_label}:${source.evidence_start}`} className="border-l-2 border-[#ccd6d1] pl-3">
                  <p className="font-medium">{source.source_label}</p>
                  <p className="mt-1 text-sm">{source.source_text}</p>
                </li>)}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </main>
  )
}
