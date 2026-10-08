import { useRef, useState, type FormEvent } from 'react'

import { createLessonFromUrl } from '../api/lesson'
import LessonViewer from './LessonViewer'
import MediaDiscovery from '../components/MediaDiscovery'
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
  const [mode, setMode] = useState<'url' | 'discovery'>('url')
  const generating = useRef(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (mode === 'url') await generateLesson(url)
  }

  async function generateLesson(selectedUrl: string) {
    if (generating.current) return
    generating.current = true
    setUrl(selectedUrl)
    setError(null)
    setIsLoading(true)

    try {
      const result = await createLessonFromUrl({
        url: selectedUrl,
        source_language: sourceLanguage,
        target_language: 'zh-CN',
        level,
        vocals: false,
      })
      setLesson(result)
    } catch {
      setError('Lesson generation failed. Please try again.')
    } finally {
      setIsLoading(false)
      generating.current = false
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

          <div role="group" className="mt-6 flex flex-wrap gap-3" aria-label="Lesson creation mode">
            <button type="button" aria-pressed={mode === 'url'} disabled={isLoading} onClick={() => setMode('url')} className="min-h-11 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white focus:ring-2 focus:ring-[#176b5b]">Paste Video URL</button>
            <button type="button" aria-pressed={mode === 'discovery'} disabled={isLoading} onClick={() => setMode('discovery')} className="min-h-11 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white focus:ring-2 focus:ring-[#176b5b]">Find a Video for Me</button>
          </div>
          <form
            className="mt-8 border-y border-[#ccd6d1] bg-white px-5 py-6 sm:px-6"
            onSubmit={handleSubmit}
          >
            {mode === 'url' && <div>
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
            </div>}

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
                  disabled={isLoading}
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
                  disabled={isLoading}
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

              {mode === 'url' && <button
                type="submit"
                disabled={isLoading}
                className="h-11 bg-[#176b5b] px-6 text-sm font-semibold text-white transition-colors hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-wait disabled:bg-[#7d9992]"
              >
                {isLoading ? 'Generating...' : 'Generate Lesson'}
              </button>}
            </div>
          </form>
          {mode === 'discovery' && <MediaDiscovery key={sourceLanguage} sourceLanguage={sourceLanguage} isGenerating={isLoading} onSelect={generateLesson} />}
          {mode === 'discovery' && isLoading && <p role="status">Generating lesson...</p>}
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
