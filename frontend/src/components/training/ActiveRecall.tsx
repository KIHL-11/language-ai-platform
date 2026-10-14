import { useEffect, useRef, useState } from 'react'

import {
  chunkMemoryClient,
  type ChunkMemory,
  type ChunkMemoryClient,
  type PracticeOutcome,
} from '../../api/chunkMemory'
import type { Lesson } from '../../types/lesson'

interface ActiveRecallProps {
  lesson: Lesson
  learnerId: string
  onComplete: () => void
  client?: ChunkMemoryClient
}

type RecallState =
  | { status: 'loading' }
  | { status: 'available'; chunks: ChunkMemory[] }
  | { status: 'empty' }
  | { status: 'unavailable' }

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

export default function ActiveRecall({
  lesson,
  learnerId,
  onComplete,
  client = chunkMemoryClient,
}: ActiveRecallProps) {
  const [recall, setRecall] = useState<RecallState>({ status: 'loading' })
  const [chunkIndex, setChunkIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submissionFailed, setSubmissionFailed] = useState(false)
  const submissionLock = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let current = true

    void client
      .fetchActiveRecallChunks(
        {
          learnerId,
          language: lesson.source_language,
          currentLessonId: lesson.id,
          limit: 5,
        },
        controller.signal,
      )
      .then((chunks) => {
        if (!current) return
        setRecall(
          chunks.length > 0
            ? { status: 'available', chunks }
            : { status: 'empty' },
        )
      })
      .catch((error: unknown) => {
        if (current && !isAbortError(error)) {
          setRecall({ status: 'unavailable' })
        }
      })

    return () => {
      current = false
      controller.abort()
    }
  }, [client, learnerId, lesson.id, lesson.source_language])

  function advance(chunks: ChunkMemory[]) {
    submissionLock.current = false
    setSubmitting(false)
    setSubmissionFailed(false)
    setRevealed(false)
    if (chunkIndex + 1 >= chunks.length) {
      onComplete()
      return
    }
    setChunkIndex((current) => current + 1)
  }

  async function submitOutcome(
    chunk: ChunkMemory,
    chunks: ChunkMemory[],
    outcome: PracticeOutcome,
  ) {
    if (submissionLock.current) return
    submissionLock.current = true
    setSubmitting(true)
    setSubmissionFailed(false)
    try {
      await client.recordChunkPractice(learnerId, chunk.id, outcome)
      if (mounted.current) advance(chunks)
    } catch {
      if (mounted.current) {
        setSubmitting(false)
        setSubmissionFailed(true)
      }
    }
  }

  if (recall.status === 'loading') {
    return (
      <section aria-labelledby="active-recall-title">
        <h2 id="active-recall-title" className="text-2xl font-semibold text-[#18211f]">
          Recall saved language
        </h2>
        <p className="mt-4 text-[#596560]" role="status">
          Loading earlier chunks…
        </p>
        <button
          type="button"
          onClick={onComplete}
          className="mt-6 h-10 border border-[#aebcb6] bg-white px-5 text-sm font-semibold text-[#293430]"
        >
          Continue without memory
        </button>
      </section>
    )
  }

  if (recall.status === 'empty' || recall.status === 'unavailable') {
    return (
      <section aria-labelledby="active-recall-title">
        <h2 id="active-recall-title" className="text-2xl font-semibold text-[#18211f]">
          Recall saved language
        </h2>
        <p className="mt-4 text-[#596560]" role="status">
          {recall.status === 'empty'
            ? 'No earlier chunks are ready for review yet.'
            : 'Saved language is unavailable right now. You can continue training.'}
        </p>
        <button
          type="button"
          onClick={onComplete}
          className="mt-6 h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white"
        >
          Complete Active Recall
        </button>
      </section>
    )
  }

  const chunk = recall.chunks[chunkIndex]

  return (
    <section aria-labelledby="active-recall-title">
      <div className="mb-5">
        <p className="text-sm tabular-nums text-[#66726e]">
          Saved chunk {chunkIndex + 1} of {recall.chunks.length}
        </p>
        <h2 id="active-recall-title" className="mt-1 text-2xl font-semibold text-[#18211f]">
          Recall saved language
        </h2>
      </div>

      <div className="border-y border-[#c9d5d0] bg-white px-5 py-5 sm:px-6">
        <p className="text-xs font-medium text-[#66726e]">Meaning</p>
        <p className="mt-2 text-lg leading-8 text-[#18211f]">
          {chunk.meaning || 'Meaning unavailable.'}
        </p>
        {revealed ? (
          <div className="mt-5 border-t border-[#dfe7e3] pt-4" aria-live="polite">
            <p className="text-xs font-medium text-[#66726e]">Saved chunk</p>
            <p className="mt-2 text-lg font-medium leading-8 text-[#18211f]">
              {chunk.text}
            </p>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setRevealed(true)}
            className="mt-5 h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white"
          >
            Reveal chunk
          </button>
        )}
      </div>

      {revealed && !submissionFailed && (
        <div className="mt-6" aria-label="Rate your recall">
          <p className="text-sm font-medium text-[#34413d]">How well did you remember it?</p>
          <div className="mt-3 flex flex-wrap gap-3">
            {([
              ['Got it', 'success'],
              ['Almost', 'partial'],
              ['Missed', 'failure'],
            ] as const).map(([label, outcome]) => (
              <button
                key={outcome}
                type="button"
                disabled={submitting}
                onClick={() => void submitOutcome(chunk, recall.chunks, outcome)}
                className="h-10 border border-[#176b5b] bg-white px-5 text-sm font-semibold text-[#176b5b] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {submissionFailed && (
        <div className="mt-6 border border-[#d9a39d] bg-[#fff5f4] p-4" role="alert">
          <p className="text-sm text-[#7d2018]">
            Your rating could not be saved. You can continue without blocking this session.
          </p>
          <button
            type="button"
            onClick={() => advance(recall.chunks)}
            className="mt-3 h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white"
          >
            Continue
          </button>
        </div>
      )}
    </section>
  )
}
