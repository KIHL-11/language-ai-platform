import { useState, type FormEvent } from 'react'

import { createLessonFromUrl } from '../api/lesson'
import LessonViewer from './LessonViewer'
import type {
  Lesson,
  LessonLevel,
  SourceLanguage,
} from '../types/lesson'

export default function LessonGenerator() {
  const [url, setUrl] = useState('')
  const [sourceLanguage, setSourceLanguage] =
    useState<SourceLanguage>('en')
  const [level, setLevel] = useState<LessonLevel>('B2')
  const [lesson, setLesson] = useState<Lesson | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      const result = await createLessonFromUrl({
        url,
        source_language: sourceLanguage,
        target_language: 'zh-CN',
        level,
        vocals: false,
      })
      setLesson(result)
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : 'Lesson generation failed. Try again.',
      )
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="min-h-screen bg-[#f3f7f5] font-['Aptos','Segoe_UI',sans-serif] text-[#18211f]">
      <header className="border-b border-[#ccd6d1] bg-white">
        <div className="mx-auto flex h-16 max-w-5xl items-center px-5 sm:px-8">
          <span className="text-sm font-semibold text-[#176b5b]">
            Language AI
          </span>
          <span className="mx-3 h-4 w-px bg-[#ccd6d1]" aria-hidden="true" />
          <span className="text-sm text-[#58635f]">Lesson Generator</span>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8 sm:py-14">
        <section aria-labelledby="generator-title">
          <h1
            id="generator-title"
            className="text-3xl font-semibold text-[#18211f] sm:text-4xl"
          >
            Generate a lesson
          </h1>

          <form
            className="mt-8 border-y border-[#ccd6d1] bg-white px-5 py-6 sm:px-6"
            onSubmit={handleSubmit}
          >
            <div>
              <label
                className="mb-2 block text-sm font-medium text-[#34413d]"
                htmlFor="youtube-url"
              >
                YouTube URL
              </label>
              <input
                id="youtube-url"
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
                required
                className="h-12 w-full border border-[#aebcb6] bg-white px-3.5 text-base outline-none transition-colors placeholder:text-[#8b9792] focus:border-[#176b5b] focus:ring-2 focus:ring-[#176b5b]/20"
              />
            </div>

            <div className="mt-5 grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
              <div className="min-w-0">
                <label
                  className="mb-2 block text-sm font-medium text-[#34413d]"
                  htmlFor="source-language"
                >
                  Source language
                </label>
                <select
                  id="source-language"
                  value={sourceLanguage}
                  onChange={(event) =>
                    setSourceLanguage(event.target.value as SourceLanguage)
                  }
                  className="h-11 w-full border border-[#aebcb6] bg-white px-3 text-sm outline-none focus:border-[#176b5b] focus:ring-2 focus:ring-[#176b5b]/20"
                >
                  <option value="en">English</option>
                  <option value="de">German</option>
                </select>
              </div>

              <div className="min-w-0">
                <label
                  className="mb-2 block text-sm font-medium text-[#34413d]"
                  htmlFor="lesson-level"
                >
                  Level
                </label>
                <select
                  id="lesson-level"
                  value={level}
                  onChange={(event) =>
                    setLevel(event.target.value as LessonLevel)
                  }
                  className="h-11 w-full border border-[#aebcb6] bg-white px-3 text-sm outline-none focus:border-[#176b5b] focus:ring-2 focus:ring-[#176b5b]/20"
                >
                  <option value="B1">B1</option>
                  <option value="B2">B2</option>
                  <option value="C1">C1</option>
                </select>
              </div>

              <button
                type="submit"
                disabled={isLoading}
                className="h-11 bg-[#176b5b] px-6 text-sm font-semibold text-white transition-colors hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-wait disabled:bg-[#7d9992]"
              >
                {isLoading ? 'Generating...' : 'Generate Lesson'}
              </button>
            </div>
          </form>
        </section>

        <div className="mt-6 min-h-7" aria-live="polite">
          {error && (
            <p className="border-l-4 border-[#b42318] bg-white px-4 py-3 text-sm text-[#8f1d14]">
              {error}
            </p>
          )}
        </div>

        {lesson && <LessonViewer lesson={lesson} />}
      </div>
    </main>
  )
}
