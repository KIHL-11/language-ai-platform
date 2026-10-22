import { useEffect, useState } from 'react'

import { getIntegrationOverview, saveSpeakingSuggestion } from '../api/integration'
import { getOrCreateLearnerId } from '../session/learnerSession'
import type { IntegrationOverview, SpeakingSuggestionCandidate } from '../types/integration'
import type { SourceLanguage } from '../types/lesson'

interface Props {
  onReview: () => void
  onSpeak: (language: SourceLanguage, targetIds: string[]) => void
  onListen: (libraryItemId: string) => void
  onShadow: (libraryItemId: string, sentenceId: number) => void
}

function positionLabel(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function SuggestionSave({ learnerId, value, onSaved }: { learnerId: string; value: SpeakingSuggestionCandidate; onSaved: () => void }) {
  const initial = value.target_languages.length === 1 ? value.target_languages[0] : ''
  const [targetLanguage, setTargetLanguage] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return <li className="border-l-2 border-[#ccd6d1] pl-3">
    <p><strong>{value.text}</strong> · {value.kind === 'word' ? 'Word' : 'Chunk'} · {value.meaning || 'Meaning unavailable'}</p>
    <label className="mt-2 block text-sm">Target language
      {value.target_languages.length > 0 ? <select aria-label={`Target language for ${value.text}`} value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)}>
        <option value="">Choose target language</option>
        {value.target_languages.map((language) => <option key={language}>{language}</option>)}
      </select> : <input aria-label={`Target language for ${value.text}`} value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)} placeholder="Enter target language" maxLength={64} />}
    </label>
    <button type="button" disabled={saving || !targetLanguage.trim()} onClick={() => {
      setSaving(true); setError(null)
      void saveSpeakingSuggestion(value.speaking_session_id, value.suggestion_index, {
        user_id: learnerId, target_language: targetLanguage.trim(),
      }).then(onSaved).catch(() => setError('Suggestion was not saved.')).finally(() => setSaving(false))
    }} className="mt-2 h-9 border border-[#176b5b] px-3 disabled:opacity-50">{saving ? 'Saving…' : 'Save to Words & Chunks'}</button>
    {error && <p role="alert" className="mt-1 text-sm text-[#8f1d14]">{error}</p>}
  </li>
}

export default function TodayPage({ onReview, onSpeak, onListen, onShadow }: Props) {
  const [learnerId] = useState(getOrCreateLearnerId)
  const [language, setLanguage] = useState<SourceLanguage | 'all'>('all')
  const [overview, setOverview] = useState<IntegrationOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  function openAction(action: IntegrationOverview['next_actions'][number]) {
    if (action.type === 'review') onReview()
    else if (action.type === 'speak' && action.source_language && action.study_item_id) onSpeak(action.source_language, [action.study_item_id])
    else if (action.type === 'shadow' && action.library_item_id && action.sentence_id !== null) onShadow(action.library_item_id, action.sentence_id)
    else if (action.type === 'listen' && action.library_item_id) onListen(action.library_item_id)
  }

  useEffect(() => {
    let active = true
    void getIntegrationOverview(learnerId, language === 'all' ? undefined : language)
      .then((value) => { if (active) { setOverview(value); setError(null) } })
      .catch(() => { if (active) setError('Today is unavailable. Your learning data was not changed.') })
    return () => { active = false }
  }, [language, learnerId, revision])

  return <main className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
    <h1 className="text-3xl font-semibold">Today</h1>
    <p className="mt-2 text-sm text-[#66726e]">A factual, read-only view of what is ready to practice next.</p>
    <div className="mt-5 flex gap-2" aria-label="Today language filter">
      {(['all', 'en', 'de'] as const).map((value) => <button key={value} type="button" aria-pressed={language === value} onClick={() => setLanguage(value)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">{value === 'all' ? 'All languages' : value === 'en' ? 'English' : 'German'}</button>)}
    </div>
    {error && <p role="alert" className="mt-6 text-[#8f1d14]">{error}</p>}
    {!overview && !error && <p role="status" className="mt-8">Loading Today…</p>}
    {overview && <div className="mt-8 grid gap-6 lg:grid-cols-2">
      <section className="bg-white p-5"><h2 className="text-xl font-semibold">Daily Review</h2>
        <p className="mt-3">Due now: Words {overview.review.due_words} · Chunks {overview.review.due_chunks}</p>
        <p>New: Words {overview.review.new_words} · Chunks {overview.review.new_chunks}</p>
        <button type="button" onClick={onReview} className="mt-4 h-10 border border-[#176b5b] px-4">Review now</button>
      </section>
      <section className="bg-white p-5"><h2 className="text-xl font-semibold">Weak expressions</h2>
        {overview.weak_chunks.length ? <ul className="mt-3 space-y-3">{overview.weak_chunks.map((item) => <li key={item.study_item_id}>
          <strong>{item.text}</strong><p className="text-sm text-[#66726e]">{item.last_rating ? `Last review: ${item.last_rating}` : item.review_status} · {item.lapse_count} lapses · {item.recent_used_session_count ? 'Used in recent Speaking' : 'Not yet used in recent Speaking'} · {item.stable_source_sentence_count ? 'Stable source sync exists' : item.preferred_shadowing_source ? 'Source shadowing available' : 'No source shadowing available'}</p>
          <div className="mt-1 flex gap-2"><button type="button" onClick={() => onSpeak(item.source_language, [item.study_item_id])}>Practice in Speaking</button>{item.preferred_shadowing_source && <button type="button" onClick={() => onShadow(item.preferred_shadowing_source!.library_item_id, item.preferred_shadowing_source!.sentence_id)}>Practice in Shadowing</button>}</div>
        </li>)}</ul> : <p className="mt-3">No reviewed weak chunks are ready.</p>}
      </section>
      <section className="bg-white p-5"><h2 className="text-xl font-semibold">Continue Listening</h2>
        {overview.continue_listening.length ? <ul className="mt-3 space-y-2">{overview.continue_listening.map((item) => <li key={item.library_item_id}><button type="button" onClick={() => onListen(item.library_item_id)}>{item.title} · Continue at {positionLabel(item.last_position_seconds)}{item.current_sentence_id !== null ? ` · Sentence ${item.current_sentence_id}` : ''}</button></li>)}</ul> : <p className="mt-3">No saved media is available.</p>}
      </section>
      <section className="bg-white p-5"><h2 className="text-xl font-semibold">New expressions from Speaking</h2>
        {overview.speaking_suggestions.length ? <ul className="mt-3 space-y-4">{overview.speaking_suggestions.map((item) => <SuggestionSave key={`${item.speaking_session_id}:${item.suggestion_index}`} learnerId={learnerId} value={item} onSaved={() => setRevision((value) => value + 1)} />)}</ul> : <p className="mt-3">No unsaved Speaking suggestions.</p>}
      </section>
      <section className="bg-white p-5 lg:col-span-2"><h2 className="text-xl font-semibold">Next actions</h2>
        {overview.next_actions.length ? <ol className="mt-3 space-y-2">{overview.next_actions.map((action, index) => <li key={`${action.type}:${index}`}>{index + 1}. {action.type === 'save_speaking_suggestion' ? action.label : <button type="button" onClick={() => openAction(action)}>{action.label}</button>}</li>)}</ol> : <p className="mt-3">Save learning material or complete a practice session to see recommendations.</p>}
      </section>
    </div>}
  </main>
}
