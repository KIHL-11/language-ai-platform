import { useEffect, useRef, useState } from 'react'

import {
  compareShadowingTiming,
  getMaxRecordingSeconds,
} from '../../training/shadowing'
import { useAudioRecorder } from '../../training/useAudioRecorder'
import type { LessonSentence } from '../../types/lesson'
import SegmentPlayer from './SegmentPlayer'

interface ShadowingProps {
  sentence: LessonSentence
  sentenceIndex: number
  sentenceCount: number
  audioUrl: string | null
  onNext: () => void
}

function formatClock(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(safeSeconds % 60).padStart(2, '0')}`
}

export default function Shadowing({
  sentence,
  sentenceIndex,
  sentenceCount,
  audioUrl,
  onNext,
}: ShadowingProps) {
  const playbackRef = useRef<HTMLAudioElement>(null)
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const maxRecordingSeconds = getMaxRecordingSeconds(
    sentence.start,
    sentence.end,
  )
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
  } = useAudioRecorder(maxRecordingSeconds)
  const isFinalSentence = sentenceIndex === sentenceCount - 1
  const result =
    status === 'recorded'
      ? compareShadowingTiming(
          sentence.start,
          sentence.end,
          recordedDuration,
        )
      : null

  useEffect(() => {
    reset()
  }, [sentence.id, reset])

  function playRecording() {
    setPlaybackError(null)
    void playbackRef.current?.play().catch(() => {
      setPlaybackError('Your recording could not be played.')
    })
  }

  return (
    <section aria-labelledby="shadowing-title">
      <div className="mb-5">
        <p className="text-sm tabular-nums text-[#66726e]">
          Sentence {sentenceIndex + 1} of {sentenceCount}
        </p>
        <h2 id="shadowing-title" className="mt-1 text-2xl font-semibold text-[#18211f]">
          Listen, then speak with the rhythm
        </h2>
      </div>

      <SegmentPlayer
        audioUrl={audioUrl}
        start={sentence.start}
        end={sentence.end}
      />

      <div className="mt-5 border-y border-[#c9d5d0] bg-white px-5 py-5 sm:px-6">
        <p className="text-xs font-medium text-[#66726e]">Transcript</p>
        <p className="mt-2 text-lg leading-8 text-[#18211f]">{sentence.text}</p>
      </div>

      <div className="mt-5 border-y border-[#c9d5d0] bg-[#f8faf9] px-5 py-6 sm:px-6">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div aria-live="polite">
            <p className="font-semibold text-[#25312d]">
              {status === 'requesting_permission' && 'Waiting for microphone permission...'}
              {status === 'recording' && 'Recording'}
              {status === 'recorded' && 'Recording ready'}
              {(status === 'idle' || status === 'error') && 'Your recording'}
            </p>
            <p className="mt-1 font-mono text-2xl tabular-nums text-[#176b5b]">
              {formatClock(status === 'recorded' ? recordedDuration : elapsedSeconds)}
              {status === 'recording' && ` / ${formatClock(maxRecordingSeconds)}`}
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            {(status === 'idle' || status === 'error') && (
              <button
                type="button"
                onClick={() => void startRecording()}
                className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
              >
                Start Recording
              </button>
            )}
            {status === 'requesting_permission' && (
              <button
                type="button"
                disabled
                className="h-10 bg-[#9aaba4] px-5 text-sm font-semibold text-white"
              >
                Requesting microphone
              </button>
            )}
            {status === 'recording' && (
              <button
                type="button"
                onClick={stopRecording}
                className="h-10 bg-[#9d241b] px-5 text-sm font-semibold text-white hover:bg-[#7e1d16] focus:outline-none focus:ring-2 focus:ring-[#9d241b] focus:ring-offset-2"
              >
                Stop
              </button>
            )}
            {status === 'recorded' && playbackUrl && (
              <>
                <audio ref={playbackRef} src={playbackUrl} onError={() => setPlaybackError('Your recording could not be played.')} />
                <button
                  type="button"
                  onClick={playRecording}
                  className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
                >
                  Play My Recording
                </button>
                <button
                  type="button"
                  onClick={reset}
                  className="h-10 border border-[#aebcb6] bg-white px-5 text-sm font-semibold text-[#293430] hover:bg-[#f3f7f5] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
                >
                  Try Again
                </button>
              </>
            )}
          </div>
        </div>

        {error && <p role="alert" className="mt-4 text-sm text-[#9d241b]">{error}</p>}
        {playbackError && <p role="alert" className="mt-4 text-sm text-[#9d241b]">{playbackError}</p>}
        {status === 'recorded' && automaticallyStopped && (
          <p role="status" className="mt-4 text-sm text-[#596560]">
            Recording automatically stopped after the maximum allowed duration.
          </p>
        )}
      </div>

      {result && (
        <div className="mt-5 grid border-y border-[#c9d5d0] bg-white sm:grid-cols-3">
          <div className="px-5 py-4">
            <p className="text-xs text-[#66726e]">Target duration</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-[#25312d]">
              {result.targetDuration.toFixed(2)}s
            </p>
          </div>
          <div className="border-t border-[#dfe7e3] px-5 py-4 sm:border-l sm:border-t-0">
            <p className="text-xs text-[#66726e]">Your duration</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-[#25312d]">
              {result.recordedDuration.toFixed(2)}s
            </p>
          </div>
          <div className="border-t border-[#dfe7e3] px-5 py-4 sm:border-l sm:border-t-0">
            <p className="text-xs text-[#66726e]">Timing Match</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-[#176b5b]">
              {Math.round(result.timingScore)}%
            </p>
          </div>
        </div>
      )}

      <div className="mt-6 flex justify-end">
        <button
          type="button"
          onClick={onNext}
          disabled={status !== 'recorded'}
          className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba4]"
        >
          {isFinalSentence ? 'Complete stage' : 'Next sentence'}
        </button>
      </div>
    </section>
  )
}
