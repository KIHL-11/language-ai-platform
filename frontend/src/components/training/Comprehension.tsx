import { useMemo, useState, type FormEvent } from 'react'

import { generateComprehensionQuestion } from '../../training/comprehension'
import type { LessonSentence } from '../../types/lesson'
import SegmentPlayer from './SegmentPlayer'

interface ComprehensionProps {
  sentence: LessonSentence
  lessonSentences: LessonSentence[]
  sentenceIndex: number
  sentenceCount: number
  audioUrl: string | null
  onNext: () => void
}

export default function Comprehension({
  sentence,
  lessonSentences,
  sentenceIndex,
  sentenceCount,
  audioUrl,
  onNext,
}: ComprehensionProps) {
  const question = useMemo(
    () => generateComprehensionQuestion(sentence, lessonSentences),
    [lessonSentences, sentence],
  )
  const [response, setResponse] = useState<{
    sentenceId: number
    selectedIndex: number | null
    submitted: boolean
  }>({ sentenceId: sentence.id, selectedIndex: null, submitted: false })
  const selectedIndex =
    response.sentenceId === sentence.id ? response.selectedIndex : null
  const submitted =
    response.sentenceId === sentence.id && response.submitted
  const isCorrect = submitted && selectedIndex === question.answerIndex
  const isFinalSentence = sentenceIndex === sentenceCount - 1

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (selectedIndex === null) {
      return
    }
    setResponse({
      sentenceId: sentence.id,
      selectedIndex,
      submitted: true,
    })
  }

  return (
    <section aria-labelledby="comprehension-title">
      <div className="mb-5">
        <p className="text-sm tabular-nums text-[#66726e]">
          Sentence {sentenceIndex + 1} of {sentenceCount}
        </p>
        <h2 id="comprehension-title" className="mt-1 text-2xl font-semibold text-[#18211f]">
          Understand what you hear
        </h2>
      </div>

      <SegmentPlayer audioUrl={audioUrl} start={sentence.start} end={sentence.end} />

      <form className="mt-6 border border-[#c9d5d0] bg-white p-5 sm:p-6" onSubmit={handleSubmit}>
        <fieldset disabled={submitted}>
          <legend className="text-lg font-semibold leading-7 text-[#18211f]">
            {question.question}
          </legend>
          <div className="mt-5 space-y-2">
            {question.options.map((option, index) => (
              <label
                key={option}
                className={`flex cursor-pointer items-start gap-3 border px-4 py-3 text-sm leading-6 ${
                  selectedIndex === index
                    ? 'border-[#176b5b] bg-[#edf5f2]'
                    : 'border-[#c9d5d0] bg-white hover:bg-[#f7f9f8]'
                }`}
              >
                <input
                  type="radio"
                  name={`comprehension-${sentence.id}`}
                  value={index}
                  checked={selectedIndex === index}
                  onChange={() =>
                    setResponse({
                      sentenceId: sentence.id,
                      selectedIndex: index,
                      submitted: false,
                    })
                  }
                  className="mt-1 accent-[#176b5b]"
                />
                <span>{option}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="mt-5 flex justify-end">
          <button
            type="submit"
            disabled={selectedIndex === null || submitted}
            className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba5]"
          >
            Submit answer
          </button>
        </div>
      </form>

      {submitted && (
        <div
          className={`mt-5 border-l-4 bg-white px-5 py-4 ${
            isCorrect ? 'border-[#176b5b]' : 'border-[#b42318]'
          }`}
          aria-live="polite"
        >
          <p className={`font-semibold ${isCorrect ? 'text-[#135c4e]' : 'text-[#9d241b]'}`}>
            {isCorrect ? 'Correct' : 'Incorrect'}
          </p>
          {!isCorrect && (
            <p className="mt-2 text-sm text-[#596560]">
              Correct answer: {question.options[question.answerIndex]}
            </p>
          )}
          <p className="mt-3 text-sm leading-6 text-[#34413d]">
            The original sentence is: <span className="font-medium">{sentence.text}</span>
          </p>
        </div>
      )}

      <div className="mt-5 flex justify-end">
        <button
          type="button"
          onClick={onNext}
          disabled={!submitted}
          className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba5]"
        >
          {isFinalSentence ? 'Complete stage' : 'Next sentence'}
        </button>
      </div>
    </section>
  )
}
