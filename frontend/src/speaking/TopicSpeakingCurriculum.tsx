import { useEffect, useRef, useState } from 'react'

import { finishTopicAttempt, listTopics, previewTopic, sendTopicTurn, startTopicAttempt } from '../api/topics'
import type { LessonLevel, SourceLanguage } from '../types/lesson'
import type { SpeakingTopic, TopicAttemptState, TopicFeedback, TopicPreview } from '../types/topics'


interface Props { learnerId: string; onBack: () => void }

export default function TopicSpeakingCurriculum({ learnerId, onBack }: Props) {
  const [language, setLanguage] = useState<SourceLanguage>('en')
  const [level, setLevel] = useState<LessonLevel>('B1')
  const [topics, setTopics] = useState<SpeakingTopic[]>([])
  const [preview, setPreview] = useState<TopicPreview | null>(null)
  const [selectedTargets, setSelectedTargets] = useState<string[]>([])
  const [consent, setConsent] = useState(false)
  const [state, setState] = useState<TopicAttemptState | null>(null)
  const [feedback, setFeedback] = useState<TopicFeedback | null>(null)
  const [transcript, setTranscript] = useState<Array<{ role: 'learner' | 'agent'; text: string }>>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef<TopicAttemptState | null>(null)
  const pending = useRef<Promise<unknown>>(Promise.resolve())

  useEffect(() => () => {
    void pending.current.catch(() => undefined).then(() => {
      const current = latest.current
      if (current?.phase === 'live') {
        latest.current = null
        void finishTopicAttempt(current.topic_id, current, learnerId, true).catch(() => undefined)
      }
    })
  }, [learnerId])

  useEffect(() => {
    let active = true
    void listTopics(language, level)
      .then((values) => { if (active) setTopics(values) })
      .catch(() => { if (active) { setTopics([]); setError('The topic catalog is unavailable.') } })
    return () => { active = false }
  }, [language, level])

  async function choose(topic: SpeakingTopic) {
    setBusy(true); setError(null)
    try {
      const value = await previewTopic(topic.topic_id, learnerId)
      setPreview(value); setSelectedTargets(value.target_chunks.map((target) => target.study_item_id)); setConsent(false)
    } catch { setError('The topic preview or your saved expressions could not be loaded.') }
    finally { setBusy(false) }
  }

  async function start() {
    if (!preview || !consent) return
    setBusy(true); setError(null)
    try {
      const request = startTopicAttempt(preview.topic.topic_id, learnerId, selectedTargets)
      pending.current = request
      const value = await request
      latest.current = value
      setState(value); setTranscript([{ role: 'agent', text: value.prompt ?? preview.topic.guided_prompts[0] }])
    } catch (caught) {
      setError((caught as { status?: number }).status === 503
        ? 'Topic attempts are unavailable in cloud mode until owner-scoped Speaking storage is implemented.'
        : 'Topic practice could not start.')
    } finally { setBusy(false) }
  }

  async function send() {
    const value = text.trim()
    if (!preview || !state || !value) return
    setBusy(true); setError(null)
    try {
      const request = sendTopicTurn(preview.topic.topic_id, state, learnerId, value)
      pending.current = request
      const result = await request
      latest.current = result.state
      setState(result.state); setTranscript((items) => [...items, { role: 'learner', text: value }, { role: 'agent', text: result.agent_reply }]); setText('')
    } catch { setError('The finalized learner turn was not saved. Please retry.') }
    finally { setBusy(false) }
  }

  async function finish(interrupted: boolean) {
    if (!preview || !state) return
    setBusy(true); setError(null)
    try {
      const request = finishTopicAttempt(preview.topic.topic_id, state, learnerId, interrupted)
      pending.current = request
      const result = await request
      latest.current = result.state
      setState(result.state); setFeedback(result.feedback)
    } catch { setError('The attempt ended, but feedback could not be saved.') }
    finally { setBusy(false) }
  }

  if (feedback && preview) return <main className="mx-auto max-w-4xl px-5 py-8">
    <h1 className="text-3xl font-semibold">Topic practice feedback</h1>
    <p className="mt-2">{preview.topic.label} · {feedback.completion === 'interrupted' ? 'Interrupted' : 'Completed'} · {feedback.learner_turn_count} learner turns</p>
    <section className="mt-6 bg-white p-5"><h2 className="text-xl font-semibold">Checkpoint lexical clues</h2><p>Term matches are clues, not proof of task completion.</p><ul className="mt-3 space-y-3">{feedback.checkpoints.map((item) => <li key={item.checkpoint_id}><strong>{item.observed ? 'Term observed' : 'No term evidenced'}:</strong> {item.label}{item.evidence && <blockquote className="mt-1 border-l-2 pl-3">“{item.evidence}”</blockquote>}</li>)}</ul></section>
    <section className="mt-5 bg-white p-5"><h2 className="text-xl font-semibold">Expression evidence</h2><ul className="mt-3">{feedback.target_usage.map((target) => <li key={target.study_item_id}>{target.use_count ? '✓ Observed' : '○ Not observed'} — {target.text}{target.evidence[0] && <> — “{target.evidence[0]}”</>}</li>)}</ul></section>
    <section className="mt-5 bg-white p-5"><h2 className="text-xl font-semibold">Grounded corrections</h2>{feedback.corrections.length ? feedback.corrections.map((item, index) => <p key={index}>{item.learner_text} → {item.corrected_text}: {item.short_explanation}</p>) : <p>No deterministic correction was identified.</p>}</section>
    <ul className="mt-5 text-sm text-[#66726e]">{feedback.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
    <button type="button" className="mt-6" onClick={() => { setFeedback(null); setState(null); setTranscript([]); setPreview(null) }}>Choose another topic</button>
  </main>

  if (state && preview) return <main className="mx-auto max-w-4xl px-5 py-8">
    <h1 className="text-3xl font-semibold">{preview.topic.label}</h1>
    <p role="status" className="mt-2">Local deterministic mock · no microphone or provider credential</p>
    <section aria-label="Mock conversation" className="mt-6 space-y-3 bg-white p-5">{transcript.map((turn, index) => <p key={index}><strong>{turn.role === 'learner' ? 'You' : 'Partner'}:</strong> {turn.text}</p>)}</section>
    <label className="mt-5 block" htmlFor="topic-response">Your response</label><textarea id="topic-response" value={text} onChange={(event) => setText(event.target.value)} maxLength={2000} rows={4} className="w-full border p-3" />
    <div className="mt-3 flex flex-wrap gap-3"><button type="button" disabled={busy || !text.trim()} onClick={() => void send()}>Send finalized turn</button><button type="button" disabled={busy} onClick={() => void finish(false)}>Finish practice</button><button type="button" disabled={busy} onClick={() => void finish(true)}>Interrupt practice</button></div>
    {error && <p role="alert" className="mt-3 text-[#8f1d14]">{error}</p>}
  </main>

  return <main className="mx-auto max-w-5xl px-5 py-8">
    <button type="button" onClick={onBack}>← Speaking Agent</button>
    <h1 className="mt-4 text-3xl font-semibold">Topic speaking curriculum</h1>
    <p className="mt-2 text-[#66726e]">Choose a reviewed scenario. B1/B2/C1 labels describe presentation difficulty; they are not assessments.</p>
    <div className="mt-6 flex gap-4"><label>Language<select aria-label="Topic language" value={language} onChange={(event) => { setLanguage(event.target.value as SourceLanguage); setTopics([]); setPreview(null); setConsent(false); setError(null) }}><option value="en">English</option><option value="de">German</option></select></label><label>Level<select aria-label="Topic level" value={level} onChange={(event) => { setLevel(event.target.value as LessonLevel); setTopics([]); setPreview(null); setConsent(false); setError(null) }}><option>B1</option><option>B2</option><option>C1</option></select></label></div>
    {error && <p role="alert" className="mt-4 text-[#8f1d14]">{error}</p>}
    {!preview ? <section aria-label="Available topics" className="mt-6 grid gap-4 md:grid-cols-2">{topics.map((topic) => <article key={topic.topic_id} className="bg-white p-5"><p className="text-sm">{topic.language.toUpperCase()} · {topic.level}</p><h2 className="mt-1 text-xl font-semibold">{topic.label}</h2><p className="mt-2">{topic.scenario}</p><button type="button" disabled={busy} className="mt-4" onClick={() => void choose(topic)}>Preview {topic.label}</button></article>)}</section> : <section className="mt-6 bg-white p-6"><button type="button" onClick={() => { setPreview(null); setConsent(false) }}>← Topics</button><h2 className="mt-3 text-2xl font-semibold">{preview.topic.label}</h2><p className="mt-2"><strong>Goal:</strong> {preview.topic.goal}</p><p className="mt-2"><strong>Your role:</strong> {preview.topic.learner_role}</p><p><strong>Partner:</strong> {preview.topic.partner_role}</p><h3 className="mt-4 font-semibold">Task checkpoints</h3><ul>{preview.topic.success_criteria.map((item) => <li key={item.id}>{item.label}</li>)}</ul><h3 className="mt-4 font-semibold">Your active saved chunks</h3>{preview.target_chunks.length ? <ul>{preview.target_chunks.map((target) => <li key={target.study_item_id}><label><input type="checkbox" checked={selectedTargets.includes(target.study_item_id)} onChange={() => setSelectedTargets((items) => items.includes(target.study_item_id) ? items.filter((id) => id !== target.study_item_id) : [...items, target.study_item_id])} /> {target.text}</label></li>)}</ul> : <p>No active saved chunks match this preview. Practice can continue without targets.</p>}<p className="mt-4 text-sm">{preview.evidence_notice}</p><label className="mt-4 block"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /> I am ready to start this local mock practice.</label><button type="button" disabled={busy || !consent} className="mt-4" onClick={() => void start()}>Start mock topic practice</button></section>}
  </main>
}
