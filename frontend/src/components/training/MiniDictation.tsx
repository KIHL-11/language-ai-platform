import { useState, type FormEvent } from 'react'

import { compareDictation, type DictationResult } from '../../training/dictation'
import type { LessonSentence } from '../../types/lesson'
import SegmentPlayer from './SegmentPlayer'

interface MiniDictationProps {
  sentence: LessonSentence
  sentenceIndex: number
  sentenceCount: number
  audioUrl: string | null
  onNext: () => void
}

export default function MiniDictation({
  sentence,
  sentenceIndex,
  sentenceCount,
  audioUrl,
  onNext,
}: MiniDictationProps) {
  const [attempt, setAttempt] = useState<{
    sentenceId: number
    answer: string
    result: DictationResult | null
  }>({ sentenceId: sentence.id, answer: '', result: null })
  const answer = attempt.sentenceId === sentence.id ? attempt.answer : ''
  const result = attempt.sentenceId === sentence.id ? attempt.result : null
  const isFinalSentence = sentenceIndex === sentenceCount - 1

  function handleCheck(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempt({
      sentenceId: sentence.id,
      answer,
      result: compareDictation(sentence.text, answer),
    })
  }

  function tryAgain() {
    setAttempt({ sentenceId: sentence.id, answer: '', result: null })
  }

  return (
    <section aria-labelledby="mini-dictation-title">
      <div className="mb-5">
        <p className="text-sm tabular-nums text-[#66726e]">
          Sentence {sentenceIndex + 1} of {sentenceCount}
        </p>
        <h2 id="mini-dictation-title" className="mt-1 text-2xl font-semibold text-[#18211f]">
          Write what you hear
        </h2>
      </div>

      <SegmentPlayer audioUrl={audioUrl} start={sentence.start} end={sentence.end} />

      <form className="mt-6" onSubmit={handleCheck}>
        <label htmlFor="dictation-answer" className="mb-2 block text-sm font-medium text-[#34413d]">
          Your answer
        </label>
        <textarea
          id="dictation-answer"
          rows={4}
          value={answer}
          onChange={(event) =>
            setAttempt({
              sentenceId: sentence.id,
              answer: event.target.value,
              result: null,
            })
          }
          disabled={result !== null}
          className="w-full resize-y border border-[#aebcb6] bg-white px-3.5 py-3 text-base leading-7 outline-none placeholder:text-[#8b9792] focus:border-[#176b5b] focus:ring-2 focus:ring-[#176b5b]/20 disabled:bg-[#f5f7f6]"
          placeholder="Type the sentence you heard"
        />
        <div className="mt-3 flex justify-end">
          <button
            type="submit"
            disabled={answer.trim().length === 0 || result !== null}
            className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba5]"
          >
            Check
          </button>
        </div>
      </form>

      {result && (
        <div className="mt-6 border border-[#c9d5d0] bg-white p-5 sm:p-6" aria-live="polite">
          <div className="flex flex-wrap items-end justify-between gap-4 border-b border-[#dfe7e3] pb-4">
            <div>
              <p className="text-sm text-[#66726e]">Accuracy</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums text-[#18211f]">
                {result.accuracy}%
              </p>
            </div>
            <div className="flex gap-4 text-xs text-[#66726e]">
              <span>{result.correctWords.length} correct</span>
              <span>{result.missingIncorrectWords.length} missing</span>
              <span>{result.extraWords.length} extra</span>
            </div>
          </div>

          <div className="mt-5">
            <p className="text-xs font-medium text-[#66726e]">Target</p>
            <p className="mt-1 leading-7 text-[#18211f]">{sentence.text}</p>
            <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Target word comparison">
              {result.targetWords.map((word, index) => (
                <span
                  key={`${word.value}-${index}`}
                  className={`px-1.5 py-0.5 text-sm ${
                    word.status === 'correct'
                      ? 'bg-[#e2eee9] text-[#135c4e]'
                      : 'bg-[#fbe7e5] text-[#9d241b] line-through'
                  }`}
                >
                  {word.value}
                </span>
              ))}
            </div>
          </div>

          <div className="mt-5">
            <p className="text-xs font-medium text-[#66726e]">Your answer</p>
            <p className="mt-1 leading-7 text-[#18211f]">{answer}</p>
            <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Answer word comparison">
              {result.answerWords.map((word, index) => (
                <span
                  key={`${word.value}-${index}`}
                  className={`px-1.5 py-0.5 text-sm ${
                    word.status === 'correct'
                      ? 'bg-[#e2eee9] text-[#135c4e]'
                      : 'bg-[#fff1d6] text-[#8a5707]'
                  }`}
                >
                  {word.value}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="mt-5 flex flex-wrap justify-end gap-3">
        <button
          type="button"
          onClick={tryAgain}
          disabled={result === null}
          className="h-10 border border-[#aebcb6] bg-white px-4 text-sm font-semibold text-[#293430] hover:bg-[#f3f7f5] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:text-[#9aaba5]"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={result === null}
          className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba5]"
        >
          {isFinalSentence ? 'Complete stage' : 'Next sentence'}
        </button>
      </div>
    </section>
  )
}
