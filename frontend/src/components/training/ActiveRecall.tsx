import { useState, type FormEvent } from 'react'

import {
  compareRecallAnswer,
  normalizeDictationText,
  type DictationResult,
} from '../../training/dictation'
import type { LessonSentence } from '../../types/lesson'

interface ActiveRecallProps {
  sentence: LessonSentence
  sentenceIndex: number
  sentenceCount: number
  onNext: () => void
}

export default function ActiveRecall({
  sentence,
  sentenceIndex,
  sentenceCount,
  onNext,
}: ActiveRecallProps) {
  const [attempt, setAttempt] = useState<{
    sentenceId: number
    answer: string
    result: DictationResult | null
  }>({ sentenceId: sentence.id, answer: '', result: null })
  const answer = attempt.sentenceId === sentence.id ? attempt.answer : ''
  const result = attempt.sentenceId === sentence.id ? attempt.result : null
  const isFinalSentence = sentenceIndex === sentenceCount - 1
  const safeChunks = sentence.chunks
    .filter(
      (chunk) =>
        normalizeDictationText(chunk.text) !==
        normalizeDictationText(sentence.text),
    )
    .slice(0, 2)

  function handleCheck(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempt({
      sentenceId: sentence.id,
      answer,
      result: compareRecallAnswer(sentence.text, answer),
    })
  }

  return (
    <section aria-labelledby="active-recall-title">
      <div className="mb-5">
        <p className="text-sm tabular-nums text-[#66726e]">
          Sentence {sentenceIndex + 1} of {sentenceCount}
        </p>
        <h2 id="active-recall-title" className="mt-1 text-2xl font-semibold text-[#18211f]">
          Retrieve the sentence
        </h2>
      </div>

      <div className="border-y border-[#c9d5d0] bg-white px-5 py-5 sm:px-6">
        <p className="text-xs font-medium text-[#66726e]">Meaning</p>
        <p className="mt-2 text-lg leading-8 text-[#18211f]">
          {sentence.translation || 'Translation unavailable.'}
        </p>

        {safeChunks.length > 0 && (
          <div className="mt-5 border-t border-[#dfe7e3] pt-4">
            <p className="text-xs font-medium text-[#66726e]">Useful chunks</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {safeChunks.map((chunk) => (
                <li key={chunk.text} className="border border-[#c9d5d0] bg-[#f3f7f5] px-3 py-2 text-sm text-[#34413d]">
                  <span className="font-medium">{chunk.text}</span>
                  {chunk.meaning && <span className="ml-2 text-[#66726e]">{chunk.meaning}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <form className="mt-6" onSubmit={handleCheck}>
        <label htmlFor="recall-answer" className="mb-2 block text-sm font-medium text-[#34413d]">
          Rebuild the original sentence
        </label>
        <textarea
          id="recall-answer"
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
          placeholder="Write the sentence in the source language"
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
            <span className="text-sm text-[#66726e]">
              {result.correctWords.length} words matched
            </span>
          </div>

          <div className="mt-5">
            <p className="text-xs font-medium text-[#66726e]">Target</p>
            <p className="mt-1 leading-7 text-[#18211f]">{sentence.text}</p>
          </div>
          <div className="mt-4">
            <p className="text-xs font-medium text-[#66726e]">Your answer</p>
            <p className="mt-1 leading-7 text-[#18211f]">{answer}</p>
          </div>

          <div className="mt-4 flex flex-wrap gap-1.5" aria-label="Recall word comparison">
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
      )}

      <div className="mt-5 flex justify-end">
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
