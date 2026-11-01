import { useState } from 'react'

import { decideChunkSuggestion, generateChunkSuggestions } from '../api/aiChunks'
import type { AiChunkBatch, AiChunkCandidate, CandidateEdit } from '../types/aiChunks'
import type { SourceLanguage } from '../types/lesson'


interface Props { learnerId: string }

export default function AiChunkOrganizer({ learnerId }: Props) {
  const [language, setLanguage] = useState<SourceLanguage>('en')
  const [text, setText] = useState('')
  const [batch, setBatch] = useState<AiChunkBatch | null>(null)
  const [edits, setEdits] = useState<Record<string, CandidateEdit>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function generate() {
    setBusy('generate')
    try {
      const result = await generateChunkSuggestions(learnerId, language, text)
      setBatch(result)
      setEdits(Object.fromEntries(result.candidates.map((candidate) => [candidate.candidate_id, {
        text: candidate.text,
        suggested_meaning_zh: candidate.suggested_meaning_zh,
        usage_note: candidate.usage_note,
        example: candidate.example,
      }])))
      setError(null)
      setNotice(null)
    } catch { setError('Chunk candidates could not be generated.') }
    finally { setBusy(null) }
  }

  function updateCandidate(candidate: AiChunkCandidate) {
    setBatch((current) => current ? {
      ...current,
      candidates: current.candidates.map((value) => value.candidate_id === candidate.candidate_id ? candidate : value),
    } : current)
  }

  async function decide(candidate: AiChunkCandidate, action: 'accept' | 'edit_and_accept' | 'reject') {
    if (!batch) return
    setBusy(candidate.candidate_id)
    try {
      const result = await decideChunkSuggestion(
        learnerId, batch.batch_id, candidate.candidate_id, candidate.version, action,
        action === 'edit_and_accept' ? edits[candidate.candidate_id] : undefined,
      )
      updateCandidate(result.candidate)
      setError(null)
      setNotice(result.warnings?.length ? result.warnings.join(' ') : null)
    } catch { setError('That candidate decision could not be saved. Refresh and try again.') }
    finally { setBusy(null) }
  }

  return <section className="mt-8 border-y border-[#ccd6d1] bg-white p-5" aria-labelledby="ai-chunk-title">
    <h2 id="ai-chunk-title" className="text-xl font-semibold">AI Chunk Organizer</h2>
    <p className="mt-1 text-sm text-[#66726e]">Mock mode proposes source-grounded expressions. Nothing is saved until you accept it.</p>
    <div className="mt-4 flex gap-2">
      {(['en', 'de'] as const).map((value) => <button key={value} type="button" aria-label={`Candidate language: ${value === 'en' ? 'English' : 'German'}`} aria-pressed={language === value} onClick={() => setLanguage(value)} className="h-9 border border-[#aebcb6] px-3 aria-pressed:bg-[#dce9e4]">{value === 'en' ? 'English' : 'German'}</button>)}
    </div>
    <label className="mt-4 block font-medium" htmlFor="ai-chunk-text">Learning text</label>
    <textarea id="ai-chunk-text" value={text} onChange={(event) => setText(event.target.value)} maxLength={2000} rows={4} className="mt-1 w-full border border-[#aebcb6] p-3" />
    <button type="button" disabled={busy !== null || !text.trim()} onClick={() => void generate()} className="mt-3 h-10 bg-[#176b5b] px-4 text-white disabled:opacity-50">{busy === 'generate' ? 'Generating…' : 'Generate candidates'}</button>
    {error && <p role="alert" className="mt-3 text-[#8f1d14]">{error}</p>}
    {notice && <p role="status" className="mt-3 text-[#8a5b00]">{notice}</p>}
    {batch && batch.candidates.length === 0 && <p className="mt-4">No source-grounded Chunk candidates were found.</p>}
    <ul className="mt-5 space-y-4">
      {batch?.candidates.map((candidate) => {
        const edit = edits[candidate.candidate_id]
        const proposed = candidate.state === 'proposed'
        return <li key={candidate.candidate_id} className="border border-[#ccd6d1] p-4">
          <p className="text-sm font-medium uppercase text-[#66726e]">{candidate.state.replaceAll('_', ' ')}</p>
          {proposed && edit ? <>
            <label className="mt-2 block text-sm" htmlFor={`candidate-text-${candidate.candidate_id}`}>Chunk</label>
            <input id={`candidate-text-${candidate.candidate_id}`} value={edit.text} onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, text: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            <label className="mt-2 block text-sm" htmlFor={`candidate-meaning-${candidate.candidate_id}`}>Chinese explanation</label>
            <input id={`candidate-meaning-${candidate.candidate_id}`} value={edit.suggested_meaning_zh} placeholder="Unavailable — add or confirm a meaning" onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, suggested_meaning_zh: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            <p className="mt-2 text-sm">Context: “{candidate.evidence.quote}”</p>
            <label className="mt-2 block text-sm" htmlFor={`candidate-usage-${candidate.candidate_id}`}>Usage note</label>
            <input id={`candidate-usage-${candidate.candidate_id}`} value={edit.usage_note} onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, usage_note: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            <label className="mt-2 block text-sm" htmlFor={`candidate-example-${candidate.candidate_id}`}>Example</label>
            <input id={`candidate-example-${candidate.candidate_id}`} value={edit.example} onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, example: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            {candidate.existing_study_item_id && <p className="mt-1 text-sm text-[#8a5b00]">Already in Study Bank{candidate.existing_study_item_active === false ? ' (archived; accepting will not restore it)' : ''}.</p>}
            <div className="mt-3 flex gap-2">
              <button type="button" disabled={busy !== null} onClick={() => void decide(candidate,
                edit.text === candidate.text && edit.suggested_meaning_zh === candidate.suggested_meaning_zh &&
                edit.usage_note === candidate.usage_note && edit.example === candidate.example
                  ? 'accept' : 'edit_and_accept')}
                className="h-9 bg-[#176b5b] px-3 text-white disabled:opacity-50">Accept</button>
              <button type="button" disabled={busy !== null} onClick={() => void decide(candidate, 'reject')} className="h-9 border border-[#8f1d14] px-3 text-[#8f1d14] disabled:opacity-50">Reject</button>
            </div>
          </> : <p className="mt-2 font-semibold">{candidate.text}</p>}
        </li>
      })}
    </ul>
  </section>
}
