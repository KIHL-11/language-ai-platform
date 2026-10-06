import ChunkList from './ChunkList'
import KeywordList from './KeywordList'
import type { LessonSentence } from '../types/lesson'

interface SentenceCardProps {
  sentence: LessonSentence
}

const STATUS_STYLES: Record<LessonSentence['ai_status'], string> = {
  ok: 'bg-[#e2eee9] text-[#135c4e]',
  failed: 'bg-[#fbe7e5] text-[#9d241b]',
}

const STATUS_LABELS: Record<LessonSentence['ai_status'], string> = {
  ok: 'AI complete',
  failed: 'AI failed',
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds - minutes * 60
  return `${minutes}:${remainingSeconds.toFixed(1).padStart(4, '0')}`
}

export default function SentenceCard({ sentence }: SentenceCardProps) {
  return (
    <article className="border border-[#c9d5d0] bg-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#dfe7e3] px-5 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-[#176b5b]">
            Sentence {sentence.id}
          </span>
          <span className="text-xs tabular-nums text-[#6d7975]">
            {formatTime(sentence.start)} - {formatTime(sentence.end)}
          </span>
        </div>
        <span className={`px-2 py-1 text-xs font-medium ${STATUS_STYLES[sentence.ai_status]}`}>
          {STATUS_LABELS[sentence.ai_status]}
        </span>
      </header>

      <div className="px-5 py-5 sm:px-6 sm:py-6">
        <section aria-labelledby={`sentence-${sentence.id}-original`}>
          <h3
            id={`sentence-${sentence.id}-original`}
            className="text-xs font-medium text-[#6d7975]"
          >
            Original
          </h3>
          <p className="mt-2 text-lg font-medium leading-8 text-[#18211f] sm:text-xl">
            {sentence.text}
          </p>
        </section>

        <section className="mt-5 border-l-4 border-[#176b5b] pl-4" aria-label="Translation">
          <p className="text-xs font-medium text-[#6d7975]">Translation</p>
          <p className="mt-1 text-base leading-7 text-[#34413d]">
            {sentence.translation || 'Translation unavailable.'}
          </p>
        </section>

        <div className="mt-7 grid gap-7 lg:grid-cols-2 lg:gap-8">
          <section aria-labelledby={`sentence-${sentence.id}-keywords`}>
            <h3
              id={`sentence-${sentence.id}-keywords`}
              className="mb-3 text-sm font-semibold text-[#293430]"
            >
              Keywords
            </h3>
            <KeywordList keywords={sentence.keywords} />
          </section>

          <section aria-labelledby={`sentence-${sentence.id}-chunks`}>
            <h3
              id={`sentence-${sentence.id}-chunks`}
              className="mb-3 text-sm font-semibold text-[#293430]"
            >
              Chunks
            </h3>
            <ChunkList chunks={sentence.chunks} />
          </section>
        </div>

        <section
          className="mt-7 border-t border-[#dfe7e3] pt-5"
          aria-labelledby={`sentence-${sentence.id}-grammar`}
        >
          <h3
            id={`sentence-${sentence.id}-grammar`}
            className="text-sm font-semibold text-[#293430]"
          >
            Grammar
          </h3>
          <p className="mt-2 text-sm leading-6 text-[#4f5c57]">
            {sentence.grammar || 'Grammar analysis unavailable.'}
          </p>
        </section>
      </div>
    </article>
  )
}
