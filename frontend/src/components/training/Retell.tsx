import { useMemo, useRef, useState } from 'react'

import {
  evaluateRetellDuration,
  RETELL_TARGET_MAX_SECONDS,
  RETELL_TARGET_MIN_SECONDS,
  type RetellTimingStatus,
} from '../../training/retell'
import { useAudioRecorder } from '../../training/useAudioRecorder'
import type { Chunk, Keyword, Lesson } from '../../types/lesson'

interface RetellProps {
  lesson: Lesson
  onComplete: () => void
}

type RetellPhase = 'prepare' | 'record' | 'review' | 'complete'

const SELF_REVIEW_ITEMS = [
  'I covered the main idea.',
  'I included at least one important detail.',
  'I reused at least one useful expression or chunk.',
] as const

function formatClock(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(safeSeconds % 60).padStart(2, '0')}`
}

function timingFeedback(
  status: RetellTimingStatus,
  automaticallyStopped: boolean,
): string {
  if (automaticallyStopped) {
    return 'Reached the 45-second practice limit.'
  }
  if (status === 'too_short') {
    return 'Shorter than the 30–45 second target.'
  }
  if (status === 'target_range') {
    return 'Within the 30–45 second target.'
  }
  return 'Longer than the 30–45 second target.'
}

function uniqueBy<T>(items: T[], key: (item: T) => string): T[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    const value = key(item).trim().toLocaleLowerCase()
    if (!value || seen.has(value)) {
      return false
    }
    seen.add(value)
    return true
  })
}

export default function Retell({ lesson, onComplete }: RetellProps) {
  const playbackRef = useRef<HTMLAudioElement>(null)
  const [phase, setPhase] = useState<RetellPhase>('prepare')
  const [referenceRevealed, setReferenceRevealed] = useState(false)
  const [selfReview, setSelfReview] = useState<boolean[]>(() =>
    SELF_REVIEW_ITEMS.map(() => false),
  )
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const {
    status,
    recordedDuration,
    playbackUrl,
    elapsedSeconds,
    error,
    automaticallyStopped,
    startRecording,
    stopRecording,
    reset,
  } = useAudioRecorder(RETELL_TARGET_MAX_SECONDS)
  const keywords = useMemo(
    () => uniqueBy<Keyword>(lesson.sentences.flatMap((sentence) => sentence.keywords), (keyword) => keyword.word),
    [lesson.sentences],
  )
  const chunks = useMemo(
    () => uniqueBy<Chunk>(lesson.sentences.flatMap((sentence) => sentence.chunks), (chunk) => chunk.text),
    [lesson.sentences],
  )
  const timingStatus = evaluateRetellDuration(recordedDuration)
  const visiblePhase: RetellPhase =
    phase === 'complete'
      ? 'complete'
      : status === 'recorded'
      ? 'review'
      : status === 'recording' || status === 'requesting_permission'
        ? 'record'
        : phase
  const canComplete =
    visiblePhase === 'review' && status === 'recorded' && referenceRevealed

  async function handleStart() {
    setPlaybackError(null)
    await startRecording()
  }

  function handleRetry() {
    reset()
    setPhase('prepare')
    setReferenceRevealed(false)
    setSelfReview(SELF_REVIEW_ITEMS.map(() => false))
    setPlaybackError(null)
  }

  function playRecording() {
    setPlaybackError(null)
    void playbackRef.current?.play().catch(() => {
      setPlaybackError('Your retell recording could not be played.')
    })
  }

  function handleComplete() {
    if (!canComplete) {
      return
    }
    setPhase('complete')
    onComplete()
  }

  return (
    <section aria-labelledby="retell-title">
      <div className="mb-6">
        <h2 id="retell-title" className="text-2xl font-semibold text-[#18211f]">
          Retell
        </h2>
        <p className="mt-2 max-w-2xl leading-7 text-[#596560]">
          Summarize the lesson in your own words.
        </p>
        <p className="mt-2 text-sm font-medium text-[#176b5b]">
          Target: {RETELL_TARGET_MIN_SECONDS}–{RETELL_TARGET_MAX_SECONDS} seconds
        </p>
      </div>

      {visiblePhase === 'prepare' && (
        <div>
          <div className="border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6">
            <h3 className="text-lg font-semibold text-[#25312d]">{lesson.title}</h3>
            <div className="mt-5 grid gap-6 lg:grid-cols-3">
              <div>
                <h4 className="text-sm font-semibold text-[#34413d]">Meaning notes</h4>
                <ul className="mt-2 space-y-2 text-sm leading-6 text-[#596560]">
                  {lesson.sentences.map((sentence) => (
                    <li key={sentence.id}>{sentence.translation || 'No translation available.'}</li>
                  ))}
                </ul>
              </div>
              <div>
                <h4 className="text-sm font-semibold text-[#34413d]">Keywords</h4>
                {keywords.length > 0 ? (
                  <ul className="mt-2 space-y-2 text-sm leading-6 text-[#596560]">
                    {keywords.map((keyword) => (
                      <li key={`${keyword.word}-${keyword.meaning}`}>
                        <span className="font-medium text-[#25312d]">{keyword.word}</span>: {keyword.meaning}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-sm text-[#7a8581]">No keywords available.</p>
                )}
              </div>
              <div>
                <h4 className="text-sm font-semibold text-[#34413d]">Useful chunks</h4>
                {chunks.length > 0 ? (
                  <ul className="mt-2 space-y-2 text-sm leading-6 text-[#596560]">
                    {chunks.map((chunk) => (
                      <li key={`${chunk.text}-${chunk.meaning}`}>
                        <span className="font-medium text-[#25312d]">{chunk.text}</span>: {chunk.meaning}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-sm text-[#7a8581]">No chunks available.</p>
                )}
              </div>
            </div>
          </div>

          {error && <p role="alert" className="mt-4 text-sm text-[#9d241b]">{error}</p>}
          <div className="mt-6 flex justify-end">
            <button
              type="button"
              onClick={() => void handleStart()}
              className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
            >
              Start Retell
            </button>
          </div>
        </div>
      )}

      {visiblePhase === 'record' && (
        <div className="border-y border-[#c9d5d0] bg-[#f8faf9] px-5 py-8 sm:px-6">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
            <div aria-live="polite">
              <p className="font-semibold text-[#25312d]">
                {status === 'requesting_permission' ? 'Waiting for microphone permission...' : 'Recording...'}
              </p>
              <p className="mt-2 font-mono text-2xl tabular-nums text-[#176b5b]">
                {formatClock(elapsedSeconds)} / {formatClock(RETELL_TARGET_MAX_SECONDS)}
              </p>
            </div>
            <button
              type="button"
              onClick={stopRecording}
              disabled={status !== 'recording'}
              className="h-10 bg-[#9d241b] px-5 text-sm font-semibold text-white hover:bg-[#7e1d16] focus:outline-none focus:ring-2 focus:ring-[#9d241b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#b9a09d]"
            >
              Stop
            </button>
          </div>
        </div>
      )}

      {visiblePhase === 'review' && status === 'recorded' && playbackUrl && (
        <div>
          <div className="border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm text-[#66726e]">Your retell</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-[#25312d]">
                  {recordedDuration.toFixed(1)} seconds
                </p>
                <p className="mt-2 text-sm text-[#596560]">
                  {timingFeedback(timingStatus, automaticallyStopped)}
                </p>
                {automaticallyStopped && (
                  <p role="status" className="mt-2 text-sm text-[#596560]">
                    Recording stopped at the 45-second practice limit.
                  </p>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <audio ref={playbackRef} src={playbackUrl} onError={() => setPlaybackError('Your retell recording could not be played.')} />
                <button
                  type="button"
                  onClick={playRecording}
                  className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
                >
                  Play My Retell
                </button>
                <button
                  type="button"
                  onClick={handleRetry}
                  className="h-10 border border-[#aebcb6] bg-white px-5 text-sm font-semibold text-[#293430] hover:bg-[#f3f7f5] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
                >
                  Try Again
                </button>
              </div>
            </div>
            {playbackError && <p role="alert" className="mt-4 text-sm text-[#9d241b]">{playbackError}</p>}
          </div>

          <fieldset className="mt-5 border-y border-[#c9d5d0] bg-[#f8faf9] px-5 py-6 sm:px-6">
            <legend className="font-semibold text-[#25312d]">Self-review</legend>
            <p className="mt-1 text-sm text-[#66726e]">
              These prompts are for your reflection. The app does not verify them.
            </p>
            <div className="mt-4 space-y-3">
              {SELF_REVIEW_ITEMS.map((item, index) => (
                <label key={item} className="flex items-start gap-3 text-sm text-[#34413d]">
                  <input
                    type="checkbox"
                    checked={selfReview[index]}
                    onChange={(event) => {
                      const next = [...selfReview]
                      next[index] = event.target.checked
                      setSelfReview(next)
                    }}
                    className="mt-0.5 h-4 w-4 accent-[#176b5b]"
                  />
                  <span>{item}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-5">
            {!referenceRevealed ? (
              <button
                type="button"
                onClick={() => setReferenceRevealed(true)}
                className="h-10 border border-[#176b5b] bg-white px-5 text-sm font-semibold text-[#176b5b] hover:bg-[#f3f7f5] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
              >
                Reveal Reference
              </button>
            ) : (
              <section className="border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6" aria-labelledby="retell-reference-title">
                <h3 id="retell-reference-title" className="text-lg font-semibold text-[#25312d]">
                  Reference
                </h3>
                <ol className="mt-4 space-y-5">
                  {lesson.sentences.map((sentence) => (
                    <li key={sentence.id}>
                      <p className="leading-7 text-[#18211f]">{sentence.text}</p>
                      <p className="mt-1 text-sm leading-6 text-[#66726e]">{sentence.translation}</p>
                    </li>
                  ))}
                </ol>
                {chunks.length > 0 && (
                  <div className="mt-6 border-t border-[#dfe7e3] pt-4">
                    <h4 className="text-sm font-semibold text-[#34413d]">Useful chunks</h4>
                    <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-2 text-sm text-[#596560]">
                      {chunks.map((chunk) => (
                        <li key={`${chunk.text}-${chunk.meaning}`}>
                          <span className="font-medium text-[#25312d]">{chunk.text}</span>: {chunk.meaning}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}
          </div>

          <div className="mt-6 flex justify-end">
            <button
              type="button"
              onClick={handleComplete}
              disabled={!canComplete}
              className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba4]"
            >
              Complete Retell
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
