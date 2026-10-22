import { useEffect, useRef, useState } from 'react'

import {
  getReviewQueue,
  getReviewSummary,
  rateReviewItem,
  ReviewApiError,
} from '../api/review'
import { getOrCreateLearnerId } from '../session/learnerSession'
import { formatNextReview } from '../review/formatNextReview'
import type { SourceLanguage } from '../types/lesson'
import type { ReviewRating, ReviewSummary } from '../types/review'
import type { StudyItem, StudyKind } from '../types/studyBank'


const EMPTY_COUNTS: Record<ReviewRating, number> = { again: 0, hard: 0, good: 0, easy: 0 }

function promptFor(item: StudyItem): string {
  if (item.meaning.trim()) return item.meaning
  const translation = item.provenance.find((source) => source.source_type === 'media' && source.sentence_translation.trim())
  if (translation?.source_type === 'media') return translation.sentence_translation
  return `Recall this saved ${item.kind}.`
}

export default function DailyReviewPage({ onSpeak }: { onSpeak?: (language: SourceLanguage, targetIds: string[]) => void }) {
  const [learnerId] = useState(getOrCreateLearnerId)
  const [language, setLanguage] = useState<SourceLanguage | 'all'>('all')
  const [kind, setKind] = useState<StudyKind | 'all'>('all')
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [cards, setCards] = useState<StudyItem[] | null>(null)
  const [index, setIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [counts, setCounts] = useState({ ...EMPTY_COUNTS })
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [difficultChunks, setDifficultChunks] = useState<Array<{ id: string; language: SourceLanguage }>>([])
  const submittingRef = useRef(false)

  const filters = {
    userId: learnerId,
    ...(language === 'all' ? {} : { language }),
    ...(kind === 'all' ? {} : { kind }),
  }

  async function refreshSummary() {
    try {
      const value = await getReviewSummary({
        userId: learnerId,
        ...(language === 'all' ? {} : { language }),
      })
      setSummary(value)
      setError(null)
    } catch {
      setError('Daily Review is unavailable. Please try again.')
    }
  }

  useEffect(() => {
    let current = true
    void getReviewSummary({
      userId: learnerId,
      ...(language === 'all' ? {} : { language }),
    }).then((value) => {
      if (current) { setSummary(value); setError(null) }
    }).catch(() => {
      if (current) setError('Daily Review is unavailable. Please try again.')
    })
    return () => { current = false }
  }, [language, learnerId])

  async function startReview() {
    setError(null)
    setNotice(null)
    try {
      const queue = await getReviewQueue(filters)
      setCards(queue)
      setIndex(0)
      setRevealed(false)
      setCounts({ ...EMPTY_COUNTS })
      setDifficultChunks([])
    } catch {
      setError('The Daily Review queue could not be loaded.')
    }
  }

  async function rate(rating: ReviewRating) {
    const card = cards?.[index]
    if (!card || !revealed || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    try {
      const result = await rateReviewItem(
        learnerId,
        card.id,
        rating,
        card.review.review_count,
      )
      setNotice(`Next review: ${formatNextReview(result.next_due_at).toLowerCase()}`)
      setCounts((current) => ({ ...current, [rating]: current[rating] + 1 }))
      if (card.kind === 'chunk' && (rating === 'again' || rating === 'hard')) {
        setDifficultChunks((current) => current.some((value) => value.id === card.id) ? current : [...current, { id: card.id, language: card.source_language }])
      }
      setIndex((current) => current + 1)
      setRevealed(false)
      if (index + 1 >= cards.length) await refreshSummary()
    } catch (caught) {
      if (caught instanceof ReviewApiError && caught.status === 409) {
        setError('This card changed elsewhere. The review queue has been refreshed.')
        try {
          const queue = await getReviewQueue(filters)
          setCards(queue)
          setIndex(0)
          setRevealed(false)
        } catch {
          setError('This card changed elsewhere, and the queue could not be refreshed.')
        }
      } else {
        setError('The rating was not saved. This card remains open.')
      }
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  const card = cards?.[index]
  const complete = cards !== null && index >= cards.length && cards.length > 0
  const ready = summary ? (
    kind === 'word' ? summary.due_words + summary.new_words
      : kind === 'chunk' ? summary.due_chunks + summary.new_chunks
        : summary.due_words + summary.due_chunks + summary.new_words + summary.new_chunks
  ) : 0
  const active = summary ? (
    kind === 'word' ? summary.active_words
      : kind === 'chunk' ? summary.active_chunks
        : summary.active_words + summary.active_chunks
  ) : 0
  const reviewed = counts.again + counts.hard + counts.good + counts.easy

  return (
    <main className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
      <h1 className="text-3xl font-semibold">Daily Review</h1>
      <p className="mt-2 text-sm text-[#66726e]">Review your curated Words & Chunks when they are ready.</p>
      {error && <p role="alert" className="mt-5 border border-[#d8a29d] bg-[#fff4f2] p-3 text-[#8f1d14]">{error}</p>}
      {notice && <p role="status" className="mt-5 text-sm text-[#176b5b]">{notice}</p>}

      {cards === null ? (
        <section className="mt-8 space-y-6">
          <div className="flex flex-wrap gap-2" aria-label="Daily Review filters">
            {(['all', 'en', 'de'] as const).map((value) => <button key={value} type="button" aria-pressed={language === value} onClick={() => setLanguage(value)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">{value === 'all' ? 'All languages' : value === 'en' ? 'English' : 'German'}</button>)}
            {(['all', 'word', 'chunk'] as const).map((value) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => setKind(value)} className="h-10 border border-[#aebcb6] px-4 aria-pressed:bg-[#dce9e4]">{value === 'all' ? 'All' : value === 'word' ? 'Words' : 'Chunks'}</button>)}
          </div>
          {!summary ? <p role="status">Loading Daily Review…</p> : active === 0 ? (
            <div className="border-y border-[#ccd6d1] bg-white p-6">
              <h2 className="font-semibold">No curated study items yet.</h2>
              <p className="mt-2 text-sm text-[#66726e]">Save useful Words & Chunks from Listening or My Library first.</p>
            </div>
          ) : ready === 0 ? (
            <p className="border-y border-[#ccd6d1] bg-white p-6">Nothing is ready for review right now.</p>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="border-y border-[#ccd6d1] bg-white p-5"><h2 className="font-semibold">Due now</h2><p className="mt-3">Words <strong>{summary.due_words}</strong></p><p>Chunks <strong>{summary.due_chunks}</strong></p></div>
                <div className="border-y border-[#ccd6d1] bg-white p-5"><h2 className="font-semibold">New</h2><p className="mt-3">Words <strong>{summary.new_words}</strong></p><p>Chunks <strong>{summary.new_chunks}</strong></p></div>
              </div>
              <button type="button" onClick={() => void startReview()} className="h-11 bg-[#176b5b] px-5 font-semibold text-white">Start Daily Review</button>
            </>
          )}
        </section>
      ) : complete ? (
        <section className="mt-8 border-y border-[#ccd6d1] bg-white p-6">
          <h2 className="text-2xl font-semibold">Review complete</h2>
          <p className="mt-3">Reviewed: {reviewed}</p>
          <p className="mt-2">Again: {counts.again} · Hard: {counts.hard} · Good: {counts.good} · Easy: {counts.easy}</p>
          {ready > 0 && <><p className="mt-5 font-medium">More cards are ready</p><button type="button" onClick={() => void startReview()} className="mt-3 h-10 border border-[#176b5b] px-4">Review More</button></>}
          {onSpeak && difficultChunks.length > 0 && <button type="button" onClick={() => {
            const language = difficultChunks[0].language
            onSpeak(language, difficultChunks.filter((value) => value.language === language).map((value) => value.id).slice(0, 8))
          }} className="mt-3 ml-3 h-10 border border-[#176b5b] px-4">Practice difficult chunks in Speaking</button>}
          <button type="button" onClick={() => setCards(null)} className="mt-3 ml-3 h-10 px-4">Back to summary</button>
        </section>
      ) : cards.length === 0 ? (
        <section className="mt-8 border-y border-[#ccd6d1] bg-white p-6">
          <p>{active === 0 ? 'No curated study items yet.' : 'Nothing is ready for review right now.'}</p>
          <button type="button" onClick={() => setCards(null)} className="mt-4 h-10 border border-[#176b5b] px-4">Back to summary</button>
        </section>
      ) : card ? (
        <section className="mt-8 border-y border-[#ccd6d1] bg-white p-6">
          <p className="text-sm text-[#66726e]">Card {index + 1} of {cards.length}</p>
          <p className="mt-2 text-sm font-medium">{card.kind === 'word' ? 'Word' : 'Chunk'} · {card.source_language === 'en' ? 'English' : 'German'}</p>
          {!revealed ? (
            <>
              <p className="mt-8 text-xl">{promptFor(card)}</p>
              <button type="button" onClick={() => setRevealed(true)} className="mt-8 h-11 bg-[#176b5b] px-5 font-semibold text-white">Reveal</button>
            </>
          ) : (
            <>
              <h2 className="mt-7 text-3xl font-semibold">{card.text}</h2>
              <p className="mt-2">{card.meaning || 'Meaning unavailable'} · {card.source_language === 'en' ? 'English' : 'German'}</p>
              {card.kind === 'word' && <div className="mt-4 text-sm"><p>{card.lemma && <>Lemma: {card.lemma}</>} {card.part_of_speech && <>· {card.part_of_speech}</>}</p>{card.example && <p className="mt-1">Example: {card.example}</p>}</div>}
              {card.kind === 'chunk' && card.usage && <p className="mt-4 text-sm">Usage: {card.usage}</p>}
              {card.note && <p className="mt-3 text-sm">Note: {card.note}</p>}
              {card.provenance[0]?.source_type === 'media' ? <div className="mt-5 border-l-2 border-[#ccd6d1] pl-3 text-sm"><p className="font-medium">{card.provenance[0].media_title}</p><p>{card.provenance[0].start.toFixed(1)}s · {card.provenance[0].sentence_text}</p>{card.provenance[0].sentence_translation && <p className="text-[#66726e]">{card.provenance[0].sentence_translation}</p>}</div> : card.provenance[0]?.source_type === 'speaking' ? <div className="mt-5 border-l-2 border-[#ccd6d1] pl-3 text-sm"><p className="font-medium">Speaking session</p>{card.provenance[0].evidence_text && <p>“{card.provenance[0].evidence_text}”</p>}</div> : null}
              <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
                {(['again', 'hard', 'good', 'easy'] as const).map((rating) => <button key={rating} type="button" disabled={submitting} onClick={() => void rate(rating)} className="h-11 border border-[#176b5b] px-3 disabled:opacity-50">{rating[0].toUpperCase() + rating.slice(1)}</button>)}
              </div>
            </>
          )}
        </section>
      ) : null}
    </main>
  )
}
