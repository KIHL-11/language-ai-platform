import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import { createLessonFromUrl } from '../api/lesson'
import { saveLibraryItem, updateLibraryProgress } from '../api/library'
import LessonViewer from './LessonViewer'
import MediaDiscovery from '../components/MediaDiscovery'
import type {
  LessonLevel,
  SourceLanguage,
} from '../types/lesson'
import {
  createPersonalSession,
  loadPersonalSession,
  savePersonalSession,
  type PersonalSession,
} from '../session/personalSession'
import type { TrainingSessionState } from '../training/trainingMachine'
import type { LibraryProgress } from '../types/library'

export default function LessonGenerator() {
  const [personalSession, setPersonalSession] = useState(loadPersonalSession)
  const [sessionRevision, setSessionRevision] = useState(0)
  const sessionRef = useRef(personalSession)
  const [url, setUrl] = useState(personalSession?.lesson.source_url ?? '')
  const [sourceLanguage, setSourceLanguage] =
    useState<SourceLanguage>(personalSession?.lesson.source_language ?? 'en')
  const [level, setLevel] = useState<LessonLevel>(personalSession?.lesson.level ?? 'B2')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [mode, setMode] = useState<'url' | 'discovery'>('url')
  const [libraryWarning, setLibraryWarning] = useState<string | null>(null)
  const generating = useRef(false)
  const libraryItemId = useRef<string | null>(null)
  const lastSyncedProgress = useRef<string | null>(null)
  const lastQueuedProgress = useRef<string | null>(null)
  const progressSyncTail = useRef<Promise<void>>(Promise.resolve())

  const progressForSession = useCallback((session: PersonalSession): LibraryProgress => ({
    is_training: session.isTraining,
    stage_index: session.trainingState.stageIndex,
    sentence_index: session.trainingState.sentenceIndex,
    completed_stages: session.trainingState.completedStages,
    training_order: session.trainingState.order,
    lesson_memory_imported: session.lessonMemoryImported,
  }), [])

  const queueLibraryProgress = useCallback((itemId: string, progress: LibraryProgress) => {
    const serialized = JSON.stringify(progress)
    const syncKey = `${itemId}:${serialized}`
    if (syncKey === lastSyncedProgress.current || syncKey === lastQueuedProgress.current) return
    lastQueuedProgress.current = syncKey
    progressSyncTail.current = progressSyncTail.current.then(async () => {
      await updateLibraryProgress(itemId, progress)
      lastSyncedProgress.current = syncKey
    }).catch(() => {
      // Progress persistence is best-effort and must never interrupt training.
    }).finally(() => {
      if (lastQueuedProgress.current === syncKey) lastQueuedProgress.current = null
    })
  }, [])

  const ensureLibraryItem = useCallback(async (session: PersonalSession) => {
    try {
      const progress = progressForSession(session)
      const item = await saveLibraryItem(session.lesson, progress)
      libraryItemId.current = item.id
      lastSyncedProgress.current = `${item.id}:${JSON.stringify(progress)}`
      setLibraryWarning(null)
      const current = sessionRef.current
      if (current?.lesson.id === session.lesson.id) {
        queueLibraryProgress(item.id, progressForSession(current))
      }
    } catch {
      setLibraryWarning('Lesson ready, but it could not be saved to My Library.')
    }
  }, [progressForSession, queueLibraryProgress])

  useEffect(() => {
    const restored = sessionRef.current
    if (restored) void ensureLibraryItem(restored)
  }, [ensureLibraryItem])

  function replaceSession(session: PersonalSession) {
    libraryItemId.current = null
    lastSyncedProgress.current = null
    lastQueuedProgress.current = null
    savePersonalSession(session)
    sessionRef.current = session
    setPersonalSession(session)
    setSessionRevision((current) => current + 1)
  }

  const handleSessionChange = useCallback((
    isTraining: boolean,
    trainingState: TrainingSessionState,
  ) => {
    const current = sessionRef.current
    if (!current) return
    const next = { ...current, isTraining, trainingState }
    savePersonalSession(next)
    sessionRef.current = next
    const progress = progressForSession(next)
    if (libraryItemId.current) queueLibraryProgress(libraryItemId.current, progress)
  }, [progressForSession, queueLibraryProgress])

  const handleLessonMemoryImported = useCallback(() => {
    const current = sessionRef.current
    if (!current || current.lessonMemoryImported) return
    const next = { ...current, lessonMemoryImported: true }
    savePersonalSession(next)
    sessionRef.current = next
    const progress = progressForSession(next)
    if (libraryItemId.current) queueLibraryProgress(libraryItemId.current, progress)
  }, [progressForSession, queueLibraryProgress])

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
      const session = createPersonalSession(result)
      replaceSession(session)
      void ensureLibraryItem(session)
    } catch {
      setError('Lesson generation failed. Please try again.')
    } finally {
      setIsLoading(false)
      generating.current = false
    }
  }

  return (
    <main className="min-h-screen bg-[#f3f7f5] text-[#18211f]">
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
          {libraryWarning && (
            <div className="border-l-4 border-[#b7791f] bg-white px-4 py-3 text-sm">
              <span>{libraryWarning}</span>{' '}
              <button type="button" className="font-semibold text-[#176b5b]" onClick={() => {
                const current = sessionRef.current
                if (current) void ensureLibraryItem(current)
              }}>Retry save</button>
            </div>
          )}
        </div>

        {personalSession && (
          <LessonViewer
            key={`${personalSession.lesson.id}:${sessionRevision}`}
            lesson={personalSession.lesson}
            initialIsTraining={personalSession.isTraining}
            initialTrainingState={personalSession.trainingState}
            lessonMemoryImported={personalSession.lessonMemoryImported}
            onSessionChange={handleSessionChange}
            onLessonMemoryImported={handleLessonMemoryImported}
          />
        )}
      </div>
    </main>
  )
}
