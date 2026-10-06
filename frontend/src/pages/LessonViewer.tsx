import { useState } from 'react'

import SentenceCard from '../components/SentenceCard'
import TrainingFlow from '../components/TrainingFlow'
import { API_BASE_URL } from '../api/lesson'
import { getDevStageOverride } from '../training/devStageJump'
import type { Lesson } from '../types/lesson'
import TrainingSession from './TrainingSession'

interface LessonViewerProps {
  lesson: Lesson
}

const AI_STATUS_LABELS: Record<Lesson['ai']['status'], string> = {
  complete: 'Complete',
  partial: 'Partial',
  failed: 'Failed',
}

const AI_STATUS_STYLES: Record<Lesson['ai']['status'], string> = {
  complete: 'bg-[#e2eee9] text-[#135c4e]',
  partial: 'bg-[#fff1d6] text-[#8a5707]',
  failed: 'bg-[#fbe7e5] text-[#9d241b]',
}

export default function LessonViewer({ lesson }: LessonViewerProps) {
  const [isTraining, setIsTraining] = useState(
    () =>
      getDevStageOverride(window.location.search, import.meta.env.DEV) !== null,
  )
  const audioUrl = lesson.audio_url
    ? `${API_BASE_URL}${lesson.audio_url}`
    : null

  if (isTraining) {
    return (
      <TrainingSession
        lesson={lesson}
        audioUrl={audioUrl}
        onExit={() => setIsTraining(false)}
      />
    )
  }

  return (
    <article className="mt-10 border-t border-[#aebcb6] pt-8">
      <header className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="text-sm text-[#66726e]">Generated lesson</p>
          <h2 className="mt-2 max-w-3xl text-2xl font-semibold leading-tight text-[#18211f] sm:text-3xl">
            {lesson.title}
          </h2>
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4 lg:shrink-0">
          <div>
            <dt className="text-xs text-[#66726e]">Source</dt>
            <dd className="mt-1 text-sm font-semibold text-[#293430]">
              {lesson.source_language}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[#66726e]">Target</dt>
            <dd className="mt-1 text-sm font-semibold text-[#293430]">
              {lesson.target_language}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[#66726e]">Level</dt>
            <dd className="mt-1 text-sm font-semibold text-[#293430]">
              {lesson.level}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[#66726e]">AI status</dt>
            <dd className="mt-1">
              <span className={`inline-flex px-2 py-1 text-xs font-semibold ${AI_STATUS_STYLES[lesson.ai.status]}`}>
                {AI_STATUS_LABELS[lesson.ai.status]}
              </span>
            </dd>
          </div>
        </dl>
      </header>

      {audioUrl && (
        <section className="mt-8 border-y border-[#c9d5d0] bg-white px-5 py-5 sm:px-6" aria-labelledby="lesson-audio-title">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <h3 id="lesson-audio-title" className="shrink-0 text-sm font-semibold text-[#293430]">
              Lesson audio
            </h3>
            <audio className="h-10 w-full" controls preload="metadata" src={audioUrl}>
              Your browser does not support audio playback.
            </audio>
          </div>
        </section>
      )}

      <section className="mt-10" aria-labelledby="training-flow-title">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <h3 id="training-flow-title" className="text-lg font-semibold text-[#18211f]">
            Training flow
          </h3>
          <div className="flex items-center gap-4">
            <span className="text-sm text-[#66726e]">
              {lesson.training_order.length} stages
            </span>
            <button
              type="button"
              onClick={() => setIsTraining(true)}
              className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
            >
              Start Training
            </button>
          </div>
        </div>
        <TrainingFlow stages={lesson.training_order} />
      </section>

      <section className="mt-12" aria-labelledby="sentence-list-title">
        <div className="mb-5 flex items-baseline justify-between gap-4">
          <h3 id="sentence-list-title" className="text-xl font-semibold text-[#18211f]">
            Sentence study
          </h3>
          <span className="text-sm tabular-nums text-[#66726e]">
            {lesson.sentences.length} sentences
          </span>
        </div>

        <div className="space-y-5">
          {lesson.sentences.map((sentence) => (
            <SentenceCard key={sentence.id} sentence={sentence} />
          ))}
        </div>
      </section>
    </article>
  )
}
