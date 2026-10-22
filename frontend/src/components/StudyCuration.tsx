import { useEffect, useMemo, useState } from 'react'

import {
  candidateInput,
  listStudyCandidates,
  saveStudyItems,
} from '../api/studyBank'
import { getOrCreateLearnerId } from '../session/learnerSession'
import type { LibraryItem } from '../types/library'
import type { StudyCandidate, StudyKind, StudyQuality } from '../types/studyBank'


interface StudyCurationProps {
  item: LibraryItem
  onBack: () => void
}

export default function StudyCuration({ item, onBack }: StudyCurationProps) {
  const [learnerId] = useState(getOrCreateLearnerId)
  const [candidates, setCandidates] = useState<StudyCandidate[]>([])
  const [kind, setKind] = useState<StudyKind>('word')
  const [quality, setQuality] = useState<StudyQuality | 'all'>('recommended')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    void listStudyCandidates(item.id, learnerId).then((values) => {
      if (active) setCandidates(values)
    }).catch(() => {
      if (active) setError('Words & Chunks candidates could not be loaded.')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [item.id, learnerId])

  const visible = useMemo(() => candidates.filter((candidate) =>
    candidate.kind === kind && (quality === 'all' || candidate.quality === quality),
  ), [candidates, kind, quality])

  async function save(values: StudyCandidate[]) {
    const unsaved = values.filter((candidate) => !candidate.already_saved && candidate.quality !== 'rejected')
    if (unsaved.length === 0) return
    setSaving(true)
    setError(null)
    try {
      const items = await saveStudyItems(unsaved.map((candidate) => candidateInput(learnerId, candidate)))
      const byIdentity = new Map(items.map((saved) => [`${saved.kind}:${saved.normalized_text}`, saved]))
      setCandidates((current) => current.map((candidate) => {
        const saved = byIdentity.get(`${candidate.kind}:${candidate.normalized_text}`)
        const provenanceSaved = saved?.provenance.some((source) =>
          source.source_type === 'media' &&
          source.library_item_id === candidate.provenance.library_item_id &&
          source.lesson_id === candidate.provenance.lesson_id &&
          source.sentence_id === candidate.provenance.sentence_id &&
          source.start === candidate.provenance.start &&
          source.end === candidate.provenance.end,
        ) ?? false
        return saved && provenanceSaved
          ? { ...candidate, already_saved: true, study_item_id: saved.id }
          : candidate
      }))
      setSelected(new Set())
    } catch {
      setError('Selected Words & Chunks could not be saved. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="mt-8" aria-labelledby="curation-title">
      <button type="button" onClick={onBack} className="text-sm font-semibold text-[#176b5b]">Back to Lesson</button>
      <h2 id="curation-title" className="mt-4 text-2xl font-semibold">Curate Words & Chunks</h2>
      <p className="mt-2 text-sm text-[#66726e]">Nothing is saved until you explicitly choose it.</p>
      <div className="mt-5 flex flex-wrap gap-2">
        {(['word', 'chunk'] as const).map((value) => (
          <button key={value} type="button" aria-pressed={kind === value} onClick={() => setKind(value)} className="h-10 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white">{value === 'word' ? 'Words' : 'Chunks'}</button>
        ))}
        {(['recommended', 'review', 'all'] as const).map((value) => (
          <button key={value} type="button" aria-pressed={quality === value} onClick={() => setQuality(value)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">{value === 'all' ? 'Show rejected' : value[0].toUpperCase() + value.slice(1)}</button>
        ))}
      </div>
      {loading && <p role="status" className="mt-6">Loading candidates…</p>}
      {error && <p role="alert" className="mt-6 text-[#8f1d14]">{error}</p>}
      {selected.size > 0 && (
        <button type="button" disabled={saving} onClick={() => void save(candidates.filter((candidate) => selected.has(candidate.candidate_id)))} className="mt-6 h-10 bg-[#176b5b] px-5 text-white">Save Selected ({selected.size})</button>
      )}
      <ul className="mt-6 space-y-4">
        {visible.map((candidate) => (
          <li key={candidate.candidate_id} className="border-y border-[#ccd6d1] bg-white p-5">
            <div className="flex gap-3">
              <input aria-label={`Select ${candidate.text}`} type="checkbox" disabled={candidate.already_saved || candidate.quality === 'rejected'} checked={selected.has(candidate.candidate_id)} onChange={(event) => setSelected((current) => {
                const next = new Set(current)
                if (event.target.checked) next.add(candidate.candidate_id); else next.delete(candidate.candidate_id)
                return next
              })} />
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{candidate.text}</p>
                <p className="mt-1 text-sm">{candidate.meaning || 'Meaning unavailable'}</p>
                {candidate.kind === 'word' && (candidate.lemma || candidate.part_of_speech) && <p className="mt-1 text-xs text-[#66726e]">Lemma: {candidate.lemma || '—'} · POS: {candidate.part_of_speech || '—'}</p>}
                {candidate.kind === 'chunk' && candidate.usage && <p className="mt-1 text-xs text-[#66726e]">Usage: {candidate.usage}</p>}
                <p className="mt-3 text-sm text-[#596560]">{candidate.provenance.sentence_text}</p>
                <p className="mt-1 text-sm text-[#66726e]">{candidate.provenance.sentence_translation}</p>
                <p className="mt-2 text-xs">Quality: {candidate.quality} — {candidate.quality_reasons.join('; ')}</p>
              </div>
              {candidate.already_saved ? <span className="text-sm text-[#176b5b]">Saved</span> : candidate.quality !== 'rejected' && <button type="button" disabled={saving} onClick={() => void save([candidate])} className="h-9 border border-[#176b5b] px-3">Save</button>}
            </div>
          </li>
        ))}
      </ul>
      {!loading && !error && visible.length === 0 && <p className="mt-6">No candidates match this filter.</p>}
    </section>
  )
}
