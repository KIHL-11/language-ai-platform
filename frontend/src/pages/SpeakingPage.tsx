import { useCallback, useEffect, useRef, useState } from 'react'

import { listLibraryItems } from '../api/library'
import {
  appendSpeakingEvent, createSpeakingSession, executeSpeakingTool,
  finishSpeakingSession, getSpeakingProviders, listSpeakingSessions,
  previewSpeakingContext,
} from '../api/speaking'
import { getOrCreateLearnerId } from '../session/learnerSession'
import { createSpeakingProvider, type SpeakingProviderFactory } from '../speaking/providerFactory'
import { GeminiProviderError } from '../speaking/geminiLiveProvider'
import type { SpeakingAgentProvider, SpeakingConnectionStatus, SpeakingTranscriptEvent } from '../speaking/provider'
import type { LibraryItemSummary } from '../types/library'
import type { SpeakingContext, SpeakingMode, SpeakingProviderId, SpeakingProviderInfo, SpeakingSession, SpeakingSetup } from '../types/speaking'


interface Props { providerFactory?: SpeakingProviderFactory }

const MODE_LABELS: Record<SpeakingMode, string> = {
  review_weak: 'Review weak expressions', media: 'Discuss saved media', custom: 'Custom scenario',
}

function durationLabel(seconds: number) { return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }

function providerFailureMessage(providerId: SpeakingProviderId, error: unknown): string {
  if (providerId === 'gemini-live' && error instanceof GeminiProviderError) {
    if (error.code === 'credential') return 'Gemini credential request failed. Check the backend configuration and try again.'
    if (error.code === 'quota') return 'Gemini provider quota or rate limit was reached. Try again later.'
    if (error.code === 'microphone_permission') return 'Microphone permission denied. Allow microphone access or start with Text Input.'
    if (error.code === 'microphone_unavailable') return 'No microphone is available. Start with Text Input instead.'
    if (error.code === 'setup') return 'Gemini Live setup did not complete. Retry the connection.'
    if (error.code === 'message') return 'Gemini Live returned an unreadable server message. Retry the connection.'
    return 'Gemini Live WebSocket connection failed. Check network access and try again.'
  }
  return `${providerId === 'gemini-live' ? 'Gemini Live' : 'OpenAI Realtime'} unavailable. Retry or explicitly choose another provider.`
}

export default function SpeakingPage({ providerFactory = createSpeakingProvider }: Props) {
  const [learnerId] = useState(getOrCreateLearnerId)
  const [providers, setProviders] = useState<SpeakingProviderInfo[]>([])
  const [library, setLibrary] = useState<LibraryItemSummary[]>([])
  const [recent, setRecent] = useState<SpeakingSession[]>([])
  const [providerId, setProviderId] = useState<SpeakingProviderId>('gemini-live')
  const [language, setLanguage] = useState<'en' | 'de'>('en')
  const [level, setLevel] = useState<'B1' | 'B2' | 'C1'>('B2')
  const [mode, setMode] = useState<SpeakingMode>('review_weak')
  const [duration, setDuration] = useState<180 | 300 | 600>(300)
  const [libraryId, setLibraryId] = useState<string>('')
  const [goal, setGoal] = useState('')
  const [context, setContext] = useState<SpeakingContext | null>(null)
  const [selectedTargets, setSelectedTargets] = useState<string[] | null>(null)
  const [session, setSession] = useState<SpeakingSession | null>(null)
  const [phase, setPhase] = useState<'setup' | 'connecting' | 'live' | 'review' | 'detail'>('setup')
  const [connection, setConnection] = useState<SpeakingConnectionStatus>('idle')
  const [partials, setPartials] = useState<{ learner: string; agent: string }>({ learner: '', agent: '' })
  const [remaining, setRemaining] = useState<number>(duration)
  const [muted, setMuted] = useState(false)
  const [textMode, setTextMode] = useState(false)
  const [startTextOnly, setStartTextOnly] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const providerRef = useRef<SpeakingAgentProvider | null>(null)
  const sessionRef = useRef<SpeakingSession | null>(null)
  const queueRef = useRef(Promise.resolve())
  const endingRef = useRef(false)

  useEffect(() => {
    let active = true
    void Promise.all([getSpeakingProviders(), listLibraryItems(), listSpeakingSessions(learnerId)])
      .then(([providerValues, libraryValues, sessionValues]) => { if (active) { setProviders(providerValues); setLibrary(libraryValues); setRecent(sessionValues) } })
      .catch(() => { if (active) setError('Speaking setup is unavailable. Please try again.') })
    return () => { active = false }
  }, [learnerId])

  useEffect(() => {
    if (mode === 'media' && !libraryId) return
    let active = true
    const request = { user_id: learnerId, language, level, mode, library_item_id: mode === 'media' ? libraryId : null, goal: goal || null, target_limit: 5 }
    void previewSpeakingContext(request).then((value) => { if (active) { setContext(value); setSelectedTargets(value.target_chunks.map((target) => target.study_item_id)) } }).catch(() => { if (active) setContext(null) })
    return () => { active = false }
  }, [goal, language, learnerId, level, libraryId, mode])

  const persistTurn = useCallback((event: SpeakingTranscriptEvent) => {
    if (!event.final || !event.text.trim()) return
    setPartials((value) => ({ ...value, [event.role]: '' }))
    queueRef.current = queueRef.current.then(async () => {
      const current = sessionRef.current
      if (!current) return
      try {
        const updated = await appendSpeakingEvent(current.id, learnerId, {
          expected_event_count: current.event_count, type: 'turn', role: event.role,
          text: event.text, provider_turn_id: event.providerTurnId ?? null,
        })
        sessionRef.current = updated; setSession(updated)
      } catch (caught) {
        setError((caught as { status?: number }).status === 409 ? 'Speaking session changed elsewhere. End and reopen the session.' : 'A finalized transcript could not be saved.')
      }
    })
  }, [learnerId])

  async function start() {
    if (!context) return
    setError(null); setPhase('connecting'); setConnection('connecting'); setTextMode(startTextOnly); endingRef.current = false
    const setup: SpeakingSetup = {
      user_id: learnerId, language, level, mode, provider: providerId, duration_seconds: duration,
      library_item_id: mode === 'media' ? libraryId : null, goal: goal || null,
      target_ids: selectedTargets ?? context.target_chunks.map((target) => target.study_item_id),
    }
    let created: SpeakingSession | null = null
    try {
      const value = await createSpeakingSession(setup)
      created = value.session; sessionRef.current = created; setSession(created); setRemaining(duration)
      const provider = providerFactory(providerId); providerRef.current = provider
      provider.onStatus(setConnection)
      provider.onTranscript((event) => {
        if (event.final) persistTurn(event)
        else setPartials((current) => ({ ...current, [event.role]: event.text }))
      })
      provider.onInterrupted(() => setConnection('interrupted'))
      provider.onToolCall((call) => {
        queueRef.current = queueRef.current.then(async () => {
          const current = sessionRef.current
          if (!current) return
          try {
            const value = await executeSpeakingTool(current.id, learnerId, current.event_count, call.name, call.arguments)
            if (value.session) { sessionRef.current = value.session; setSession(value.session) }
            provider.respondToTool?.(call.id, value.result)
          } catch { provider.respondToTool?.(call.id, { error: 'Tool request was rejected.' }) }
        })
      })
      provider.onError((providerError) => { setError(`${providerFailureMessage(providerId, providerError)} No other provider was started.`); void end('failed', 'error') })
      provider.onEnded((reason) => { if (!endingRef.current) void end(reason === 'time_limit' ? 'completed' : 'interrupted', reason) })
      await provider.connect({ context: value.context, durationSeconds: duration, textOnly: startTextOnly })
      setPhase('live')
    } catch (caught) {
      await providerRef.current?.disconnect('error'); providerRef.current = null
      if (created) {
        try { await finishSpeakingSession(created.id, learnerId, created.event_count, 'failed', 'error') } catch { /* best effort */ }
      }
      setPhase('setup'); setError(providerFailureMessage(providerId, caught))
    }
  }

  const end = useCallback(async (status: 'completed' | 'interrupted' | 'failed' = 'completed', reason: import('../types/speaking').SpeakingEndReason = 'user') => {
    if (endingRef.current) return
    endingRef.current = true
    await providerRef.current?.disconnect(reason); providerRef.current = null
    await queueRef.current
    const current = sessionRef.current
    if (current?.status === 'active') {
      try {
        const updated = await finishSpeakingSession(current.id, learnerId, current.event_count, status, reason)
        sessionRef.current = updated; setSession(updated); setRecent((items) => [updated, ...items.filter((item) => item.id !== updated.id)].slice(0, 10))
      } catch { setError('The session ended, but its final status could not be saved.') }
    }
    setConnection('ended'); setPhase('review')
  }, [learnerId])

  useEffect(() => {
    if (phase !== 'live') return
    const timer = window.setInterval(() => setRemaining((value) => {
      if (value <= 1) { window.clearInterval(timer); void end('completed', 'time_limit'); return 0 }
      return value - 1
    }), 1_000)
    return () => window.clearInterval(timer)
  }, [end, phase])

  useEffect(() => () => {
    endingRef.current = true
    const provider = providerRef.current
    if (provider) void provider.disconnect('page_exit')
    const current = sessionRef.current
    if (current?.status === 'active') {
      void queueRef.current.then(() => finishSpeakingSession(current.id, learnerId, sessionRef.current?.event_count ?? current.event_count, 'interrupted', 'page_exit')).catch(() => undefined)
    }
  }, [learnerId])

  function sendText() {
    const value = text.trim()
    if (!value || !providerRef.current?.sendText) return
    void providerRef.current.sendText(value); setText('')
  }

  if ((phase === 'review' || phase === 'detail') && session) return <SessionReview session={session} onBack={() => { sessionRef.current = null; setSession(null); setPhase('setup'); setConnection('idle'); endingRef.current = false }} />

  if (phase === 'live' || phase === 'connecting') return <main className="mx-auto max-w-5xl px-5 py-8">
    <h1 className="text-3xl font-semibold">Speaking</h1>
    <p className="mt-2">{providerId === 'gemini-live' ? 'Gemini Live' : 'OpenAI Realtime'} · {language.toUpperCase()} {level} · {durationLabel(remaining)}</p>
    <p role="status" className="mt-4 font-semibold">{phase === 'connecting' ? 'Connecting' : connection.replaceAll('_', ' ')}</p>
    {error && <p role="alert" className="mt-3 text-[#8f1d14]">{error}</p>}
    <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_2fr]">
      <aside className="bg-white p-5"><h2 className="font-semibold">Target expressions</h2><ul className="mt-3 space-y-2">{session?.target_usage.map((target) => <li key={target.study_item_id}>{target.use_count ? '✓ Used' : '○ Not used'} — {target.text}{target.evidence[0] && <p className="text-xs text-[#66726e]">“{target.evidence[0]}”</p>}</li>)}</ul></aside>
      <section className="bg-white p-5"><h2 className="font-semibold">Live transcript</h2><ol className="mt-3 space-y-3">{session?.turns.map((turn) => <li key={turn.sequence}><strong>{turn.role === 'learner' ? 'You' : 'Agent'}:</strong> {turn.text}</li>)}</ol>{partials.learner && <p><strong>You:</strong> {partials.learner}…</p>}{partials.agent && <p><strong>Agent:</strong> {partials.agent}…</p>}</section>
    </div>
    <div className="mt-5 flex flex-wrap gap-3">
      <button type="button" onClick={() => { const value = !muted; setMuted(value); providerRef.current?.mute(value) }}>{muted ? 'Unmute' : 'Mute'}</button>
      {providers.find((value) => value.id === providerId)?.capabilities.text_input && <button type="button" onClick={() => setTextMode((value) => !value)}>Switch to Text Input</button>}
      <button type="button" onClick={() => void end()}>End Session</button>
    </div>
    {textMode && <div className="mt-4 flex gap-2"><label className="sr-only" htmlFor="speaking-text">Text message</label><input id="speaking-text" value={text} onChange={(event) => setText(event.target.value)} className="border px-3" /><button type="button" onClick={sendText}>Send</button></div>}
    {session?.corrections.at(-1) && <aside className="mt-5 bg-[#fff8e8] p-4"><strong>Correction</strong><p>You said: {session.corrections.at(-1)?.learner_text}</p><p>Better: {session.corrections.at(-1)?.corrected_text}</p><p>Why: {session.corrections.at(-1)?.short_explanation}</p></aside>}
  </main>

  const chosen = providers.find((provider) => provider.id === providerId)
  return <main className="mx-auto max-w-5xl px-5 py-10">
    <h1 className="text-3xl font-semibold">Speaking</h1><p className="mt-2 text-[#66726e]">Voice-first intelligent conversation with explicit provider control.</p>
    {error && <p role="alert" className="mt-4 text-[#8f1d14]">{error}</p>}
    <section className="mt-7 grid gap-4 bg-white p-6 sm:grid-cols-2">
      <label>Practice mode<select aria-label="Practice mode" value={mode} onChange={(event) => setMode(event.target.value as SpeakingMode)}><option value="review_weak">Review weak expressions</option><option value="media">Discuss saved media</option><option value="custom">Custom scenario</option></select></label>
      <label>Language<select aria-label="Language" value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'de')}><option value="en">English</option><option value="de">German</option></select></label>
      <label>Level<select aria-label="Level" value={level} onChange={(event) => setLevel(event.target.value as 'B1' | 'B2' | 'C1')}><option>B1</option><option>B2</option><option>C1</option></select></label>
      <label>Provider<select aria-label="Provider" value={providerId} onChange={(event) => setProviderId(event.target.value as SpeakingProviderId)}><option value="gemini-live">Gemini Live — preferred</option><option value="openai-realtime">OpenAI Realtime — optional paid provider</option></select></label>
      <label>Session length<select aria-label="Session length" value={duration} onChange={(event) => setDuration(Number(event.target.value) as 180 | 300 | 600)}><option value="180">3 minutes</option><option value="300">5 minutes</option><option value="600">10 minutes</option></select></label>
      {mode === 'media' && <label>Saved media<select aria-label="Saved media" value={libraryId} onChange={(event) => setLibraryId(event.target.value)}><option value="">Choose media</option>{library.filter((item) => item.source_language === language).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}
      {mode === 'custom' && <label className="sm:col-span-2">Scenario or goal<input aria-label="Scenario or goal" value={goal} onChange={(event) => setGoal(event.target.value)} maxLength={1000} /></label>}
    </section>
    <section className="mt-5 bg-white p-6"><h2 className="font-semibold">Target preview</h2>{context?.target_chunks.length ? <ul className="mt-3 space-y-2">{context.target_chunks.map((target) => <li key={target.study_item_id}><label><input type="checkbox" checked={(selectedTargets ?? []).includes(target.study_item_id)} onChange={() => setSelectedTargets((values) => (values ?? []).includes(target.study_item_id) ? (values ?? []).filter((id) => id !== target.study_item_id) : [...(values ?? []), target.study_item_id])} /> {target.text}</label></li>)}</ul> : <p className="mt-3">No curated chunks are available yet. You can still start a natural conversation or choose another mode.</p>}</section>
    <p className="mt-4 text-sm">{chosen?.configured ? `${chosen.id === 'gemini-live' ? 'Gemini Live' : 'OpenAI Realtime'} configured.` : 'Selected provider is not configured.'} Provider billing, quota, and data-use terms depend on your provider account.</p>
    <label className="mt-3 block"><input type="checkbox" checked={startTextOnly} disabled={!chosen?.capabilities.text_input} onChange={(event) => setStartTextOnly(event.target.checked)} /> Start with Text Input instead of microphone</label>
    <button type="button" disabled={!context || !chosen?.configured || (mode === 'media' && !libraryId)} onClick={() => void start()} className="mt-5 h-12 bg-[#176b5b] px-6 text-white disabled:bg-[#9aaba5]">Start Speaking</button>
    <section className="mt-10"><h2 className="text-xl font-semibold">Recent sessions</h2>{recent.length === 0 ? <p className="mt-3">No speaking sessions yet.</p> : <ul className="mt-3 space-y-2">{recent.map((item) => <li key={item.id}><button type="button" onClick={() => { setSession(item); sessionRef.current = item; setPhase('detail') }}>{new Date(item.started_at).toLocaleDateString()} · {MODE_LABELS[item.mode]} · {item.learner_turn_count} learner turns · {item.target_usage.filter((target) => target.use_count).length} targets used</button></li>)}</ul>}</section>
  </main>
}

function SessionReview({ session, onBack }: { session: SpeakingSession; onBack: () => void }) {
  const used = session.target_usage.filter((target) => target.use_count > 0)
  const unused = session.target_usage.filter((target) => target.use_count === 0)
  return <main className="mx-auto max-w-4xl px-5 py-10"><h1 className="text-3xl font-semibold">Session complete</h1>
    <p className="mt-3">Duration requested: {session.requested_duration_seconds / 60} minutes · Learner turns: {session.learner_turn_count}</p>
    <h2 className="mt-6 text-xl font-semibold">Target expressions</h2><h3 className="mt-3 font-semibold">Used</h3><ul>{used.map((target) => <li key={target.study_item_id}>✓ {target.text}{target.evidence[0] && <> — “{target.evidence[0]}”</>}</li>)}</ul>
    <h3 className="mt-3 font-semibold">Practice next time</h3><ul>{unused.map((target) => <li key={target.study_item_id}>○ {target.text}</li>)}</ul>
    <h2 className="mt-6 text-xl font-semibold">Important corrections</h2><p>{session.corrections.length}</p>{session.corrections.map((item, index) => <p key={index}>{item.learner_text} → {item.corrected_text}: {item.short_explanation}</p>)}
    <h2 className="mt-6 text-xl font-semibold">Suggested expressions</h2>{session.suggestions.length ? session.suggestions.map((item, index) => <p key={index}>{item.text} — {item.meaning}</p>) : <p>None</p>}
    <details className="mt-6"><summary>Transcript</summary>{session.turns.map((turn) => <p key={turn.sequence}><strong>{turn.role === 'learner' ? 'You' : 'Agent'}:</strong> {turn.text}</p>)}</details>
    <button type="button" className="mt-6" onClick={onBack}>Back to Speaking</button>
  </main>
}
