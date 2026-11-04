import { useRef, useState } from 'react'

import { CloudConflictError } from '../auth/client'
import {
  decideChunkSuggestion,
  generateChunkSuggestions,
  generateLibraryChunkSuggestions,
  getChunkSuggestions,
  newIdempotencyKey,
} from '../api/aiChunks'
import type { AiChunkBatch, AiChunkCandidate, CandidateEdit } from '../types/aiChunks'
import type { SourceLanguage } from '../types/lesson'

// At most 12 sentences per request (server contract).
const MAX_LIBRARY_SENTENCES = 12

export interface LibraryChunkSource {
  libraryItemId: string
  title: string
  language: SourceLanguage
  sentenceIds: number[]
}

interface Props {
  learnerId: string
  // Generate from a Cloud Library lesson instead of pasted text.
  librarySource?: LibraryChunkSource
}

function editOf(candidate: AiChunkCandidate): CandidateEdit {
  return {
    text: candidate.text,
    suggested_meaning_zh: candidate.suggested_meaning_zh,
    usage_note: candidate.usage_note,
    example: candidate.example,
  }
}

function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

export default function AiChunkOrganizer({ learnerId, librarySource }: Props) {
  const [language, setLanguage] = useState<SourceLanguage>(librarySource?.language ?? 'en')
  const [text, setText] = useState('')
  const [batch, setBatch] = useState<AiChunkBatch | null>(null)
  const [edits, setEdits] = useState<Record<string, CandidateEdit>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Held until a generation succeeds, so retrying a failed or lost request
  // replays it instead of creating a second batch. A fixed per-lesson key
  // would instead pin a re-imported or expired lesson to its first batch.
  const generationKey = useRef<string | null>(null)

  function showBatch(result: AiChunkBatch) {
    setBatch(result)
    setEdits(Object.fromEntries(result.candidates.map((candidate) => [candidate.candidate_id, editOf(candidate)])))
  }

  // After a conflict: take the server's state, but keep unsaved local edits
  // on every candidate whose saved state and version did not change.
  function reconcileBatch(previous: AiChunkBatch, result: AiChunkBatch) {
    const before = new Map(previous.candidates.map((candidate) => [candidate.candidate_id, candidate]))
    setBatch(result)
    setEdits((current) => Object.fromEntries(result.candidates.map((candidate) => {
      const old = before.get(candidate.candidate_id)
      const unchanged = old && old.version === candidate.version && old.state === candidate.state
      return [candidate.candidate_id, unchanged && current[candidate.candidate_id] ? current[candidate.candidate_id] : editOf(candidate)]
    })))
  }

  async function generate() {
    setBusy('generate')
    try {
      if (librarySource) {
        generationKey.current ??= newIdempotencyKey()
        showBatch(await generateLibraryChunkSuggestions(
          learnerId, librarySource.language, librarySource.libraryItemId,
          librarySource.sentenceIds.slice(0, MAX_LIBRARY_SENTENCES), generationKey.current,
        ))
        generationKey.current = null
      } else {
        showBatch(await generateChunkSuggestions(learnerId, language, text))
      }
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
    setEdits((current) => ({ ...current, [candidate.candidate_id]: editOf(candidate) }))
  }

  async function decide(candidate: AiChunkCandidate, action: 'accept' | 'edit_and_accept' | 'edit' | 'reject') {
    if (!batch) return
    setBusy(candidate.candidate_id)
    try {
      const result = await decideChunkSuggestion(
        learnerId, batch.batch_id, candidate.candidate_id, candidate.version, action,
        action === 'edit_and_accept' || action === 'edit' ? edits[candidate.candidate_id] : undefined,
      )
      updateCandidate(result.candidate)
      setError(null)
      setNotice(result.warnings?.length ? result.warnings.join(' ') : action === 'edit' ? 'Edits saved. The candidate is still waiting for your decision.' : null)
    } catch (reason) {
      if (reason instanceof CloudConflictError) {
        // Another tab or device changed it, or the batch expired: show the
        // saved state without discarding unsaved edits on other candidates.
        try {
          const latest = await getChunkSuggestions(learnerId, batch.batch_id)
          reconcileBatch(batch, latest)
          setError(latest.status === 'expired'
            ? 'These candidates have expired. Generate them again to continue.'
            : 'This candidate was changed elsewhere. The latest saved version is shown.')
        } catch { setError('That candidate could not be saved or reloaded. Refresh and try again.') }
      } else {
        setError('That candidate decision could not be saved. Refresh and try again.')
      }
    }
    finally { setBusy(null) }
  }

  return <section className="mt-8 border-y border-[#ccd6d1] bg-white p-5" aria-labelledby="ai-chunk-title">
    <h2 id="ai-chunk-title" className="text-xl font-semibold">AI Chunk Organizer</h2>
    <p className="mt-1 text-sm text-[#66726e]">Mock mode proposes source-grounded expressions. Nothing is saved until you accept it.</p>
    {librarySource ? <>
      <p className="mt-4 text-sm">From your lesson <strong>{librarySource.title}</strong> ({librarySource.language === 'en' ? 'English' : 'German'}, {Math.min(librarySource.sentenceIds.length, MAX_LIBRARY_SENTENCES)} sentences).</p>
      <button type="button" disabled={busy !== null || librarySource.sentenceIds.length === 0} onClick={() => void generate()} className="mt-3 h-10 bg-[#176b5b] px-4 text-white disabled:opacity-50">{busy === 'generate' ? 'Generating…' : 'Find chunks in this lesson'}</button>
    </> : <>
      <div className="mt-4 flex gap-2">
        {(['en', 'de'] as const).map((value) => <button key={value} type="button" aria-label={`Candidate language: ${value === 'en' ? 'English' : 'German'}`} aria-pressed={language === value} onClick={() => setLanguage(value)} className="h-9 border border-[#aebcb6] px-3 aria-pressed:bg-[#dce9e4]">{value === 'en' ? 'English' : 'German'}</button>)}
      </div>
      <label className="mt-4 block font-medium" htmlFor="ai-chunk-text">Learning text</label>
      <textarea id="ai-chunk-text" value={text} onChange={(event) => setText(event.target.value)} maxLength={2000} rows={4} className="mt-1 w-full border border-[#aebcb6] p-3" />
      <button type="button" disabled={busy !== null || !text.trim()} onClick={() => void generate()} className="mt-3 h-10 bg-[#176b5b] px-4 text-white disabled:opacity-50">{busy === 'generate' ? 'Generating…' : 'Generate candidates'}</button>
    </>}
    {error && <p role="alert" className="mt-3 text-[#8f1d14]">{error}</p>}
    {notice && <p role="status" className="mt-3 text-[#8a5b00]">{notice}</p>}
    {batch && batch.candidates.length === 0 && <p className="mt-4">No source-grounded Chunk candidates were found.</p>}
    <ul className="mt-5 space-y-4">
      {batch?.candidates.map((candidate) => {
        const edit = edits[candidate.candidate_id]
        const proposed = candidate.state === 'proposed'
        const changed = edit !== undefined && (edit.text !== candidate.text || edit.suggested_meaning_zh !== candidate.suggested_meaning_zh ||
          edit.usage_note !== candidate.usage_note || edit.example !== candidate.example)
        const evidence = candidate.evidence
        return <li key={candidate.candidate_id} className="border border-[#ccd6d1] p-4">
          <p className="text-sm font-medium uppercase text-[#66726e]">{candidate.state.replaceAll('_', ' ')}</p>
          {evidence.source_type === 'library_sentence' && <p className="mt-1 text-xs text-[#66726e]" aria-label="Candidate source">
            Sentence {evidence.sentence_id}{typeof evidence.start === 'number' ? ` · ${formatTime(evidence.start)}` : ''} · {evidence.source_label}
          </p>}
          {proposed && edit ? <>
            <label className="mt-2 block text-sm" htmlFor={`candidate-text-${candidate.candidate_id}`}>Chunk</label>
            <input id={`candidate-text-${candidate.candidate_id}`} value={edit.text} onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, text: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            <label className="mt-2 block text-sm" htmlFor={`candidate-meaning-${candidate.candidate_id}`}>Chinese explanation</label>
            <input id={`candidate-meaning-${candidate.candidate_id}`} value={edit.suggested_meaning_zh} placeholder="Unavailable — add or confirm a meaning" onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, suggested_meaning_zh: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            <p className="mt-2 text-sm">Context: “{evidence.quote}”</p>
            <label className="mt-2 block text-sm" htmlFor={`candidate-usage-${candidate.candidate_id}`}>Usage note</label>
            <input id={`candidate-usage-${candidate.candidate_id}`} value={edit.usage_note} onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, usage_note: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            <label className="mt-2 block text-sm" htmlFor={`candidate-example-${candidate.candidate_id}`}>Example</label>
            <input id={`candidate-example-${candidate.candidate_id}`} value={edit.example} onChange={(event) => setEdits((current) => ({ ...current, [candidate.candidate_id]: { ...edit, example: event.target.value } }))} className="w-full border border-[#aebcb6] px-2 py-1" />
            {candidate.existing_study_item_id && <p className="mt-1 text-sm text-[#8a5b00]">Already in Study Bank{candidate.existing_study_item_active === false ? ' (archived; accepting will not restore it)' : ''}.</p>}
            <div className="mt-3 flex gap-2">
              <button type="button" disabled={busy !== null} onClick={() => void decide(candidate, changed ? 'edit_and_accept' : 'accept')}
                className="h-9 bg-[#176b5b] px-3 text-white disabled:opacity-50">Accept</button>
              {librarySource && <button type="button" disabled={busy !== null || !changed} onClick={() => void decide(candidate, 'edit')} className="h-9 border border-[#176b5b] px-3 text-[#176b5b] disabled:opacity-50">Save edits</button>}
              <button type="button" disabled={busy !== null} onClick={() => void decide(candidate, 'reject')} className="h-9 border border-[#8f1d14] px-3 text-[#8f1d14] disabled:opacity-50">Reject</button>
            </div>
          </> : <p className="mt-2 font-semibold">{candidate.text}</p>}
        </li>
      })}
    </ul>
  </section>
}
