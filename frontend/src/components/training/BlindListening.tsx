import { useState } from 'react'

import type { LessonSentence } from '../../types/lesson'
import SegmentPlayer from './SegmentPlayer'

interface BlindListeningProps {
  sentence: LessonSentence
  sentenceIndex: number
  sentenceCount: number
  audioUrl: string | null
  onNext: () => void
}

export default function BlindListening({
  sentence,
  sentenceIndex,
  sentenceCount,
  audioUrl,
  onNext,
}: BlindListeningProps) {
  const [revealedSentenceId, setRevealedSentenceId] = useState<number | null>(null)
  const isRevealed = revealedSentenceId === sentence.id
  const isFinalSentence = sentenceIndex === sentenceCount - 1

  return (
    <section aria-labelledby="blind-listening-title">
      <div className="mb-5">
        <p className="text-sm tabular-nums text-[#66726e]">
          Sentence {sentenceIndex + 1} of {sentenceCount}
        </p>
        <h2 id="blind-listening-title" className="mt-1 text-2xl font-semibold text-[#18211f]">
          Listen before you read
        </h2>
      </div>

      <SegmentPlayer audioUrl={audioUrl} start={sentence.start} end={sentence.end} />

      <div className="mt-6 min-h-32 border border-[#c9d5d0] bg-white p-5 sm:p-6">
        {isRevealed ? (
          <div aria-live="polite">
            <p className="text-lg font-semibold leading-8 text-[#18211f]">
              {sentence.text}
            </p>
            <p className="mt-3 border-l-4 border-[#176b5b] pl-4 leading-7 text-[#4f5c57]">
              {sentence.translation || 'Translation unavailable.'}
            </p>
          </div>
        ) : (
          <p className="text-sm leading-6 text-[#66726e]">
            The transcript stays hidden until you choose to reveal it.
          </p>
        )}
      </div>

      <div className="mt-5 flex flex-wrap justify-end gap-3">
        <button
          type="button"
          onClick={() => setRevealedSentenceId(sentence.id)}
          disabled={isRevealed}
          className="h-10 border border-[#176b5b] bg-white px-4 text-sm font-semibold text-[#176b5b] hover:bg-[#edf5f2] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-default disabled:border-[#c9d5d0] disabled:text-[#84908b]"
        >
          Reveal transcript
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!isRevealed}
          className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba5]"
        >
          {isFinalSentence ? 'Complete stage' : 'Next sentence'}
        </button>
      </div>
    </section>
  )
}
