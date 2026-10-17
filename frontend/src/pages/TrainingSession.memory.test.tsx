import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChunkMemory, ChunkMemoryClient } from '../api/chunkMemory'
import { LEARNER_ID_STORAGE_KEY } from '../session/learnerSession'
import type { Lesson } from '../types/lesson'
import TrainingSession from './TrainingSession'

const LEARNER_ID = '123e4567-e89b-42d3-a456-426614174000'

const lesson: Lesson = {
  id: 'current-lesson',
  title: 'Memory lesson',
  source_url: 'https://www.youtube.com/watch?v=current',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B1',
  audio_url: null,
  source: 'subtitles',
  sentences: [
    {
      id: 1,
      text: 'This belongs to the current lesson.',
      translation: '这是当前课程的内容。',
      start: 0,
      end: 2,
      keywords: [],
      chunks: [{ text: 'belongs to', meaning: '属于', usage: 'ownership' }],
      grammar: '',
      training: {
        blind_listening: true,
        dictation: true,
        shadowing: true,
        retell: true,
      },
      ai_status: 'ok',
    },
  ],
  training_plan: {
    active_recall: { enabled: true, count: 5 },
    blind_listening: { enabled: true },
    comprehension: { enabled: false },
    mini_dictation: { enabled: false },
    shadowing: { enabled: false },
    retell: { enabled: false },
    live_dialogue: { enabled: false },
  },
  training_order: ['active_recall', 'blind_listening'],
  ai: { provider: 'mock', status: 'complete' },
}

function oldChunk(): ChunkMemory {
  return {
    id: 'old-chunk',
    user_id: LEARNER_ID,
    text: 'from an earlier lesson',
    normalized_text: 'from an earlier lesson',
    meaning: '来自之前的课程',
    source_language: 'en',
    source_lesson_id: 'earlier-lesson',
    source_media_id: null,
    source_sentence_id: 1,
    source_stage: 'lesson',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    last_practiced_at: null,
    practice_count: 0,
    success_count: 0,
    failure_count: 0,
    mastery_score: 0,
    active: true,
  }
}

function memoryClient(chunks: ChunkMemory[] = []): ChunkMemoryClient {
  return {
    importLessonChunks: vi.fn().mockResolvedValue({ added: 1, total: 1 }),
    fetchActiveRecallChunks: vi.fn().mockResolvedValue(chunks),
    recordChunkPractice: vi.fn().mockImplementation(async () => chunks[0]),
  }
}

describe('TrainingSession persistent memory integration', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
    localStorage.clear()
    localStorage.setItem(LEARNER_ID_STORAGE_KEY, LEARNER_ID)
  })

  it('imports a lesson once with the stable learner ID across rerenders', async () => {
    const client = memoryClient()
    const imported = vi.fn()
    const view = render(
      <TrainingSession lesson={lesson} audioUrl={null} onExit={vi.fn()} memoryClient={client} devMode={false} onLessonMemoryImported={imported} />,
    )

    await waitFor(() => expect(client.importLessonChunks).toHaveBeenCalledOnce())
    expect(client.importLessonChunks).toHaveBeenCalledWith(LEARNER_ID, lesson)
    expect(imported).toHaveBeenCalledOnce()
    view.rerender(
      <TrainingSession lesson={{ ...lesson }} audioUrl={null} onExit={vi.fn()} memoryClient={client} devMode={false} />,
    )
    expect(client.importLessonChunks).toHaveBeenCalledOnce()
  })

  it('skips an already persisted lesson import after a restored page load', async () => {
    const client = memoryClient()
    const imported = vi.fn()
    render(
      <TrainingSession
        lesson={lesson}
        audioUrl={null}
        onExit={vi.fn()}
        memoryClient={client}
        devMode={false}
        lessonMemoryImported
        onLessonMemoryImported={imported}
      />,
    )

    await screen.findByText('No earlier chunks are ready for review yet.')
    expect(client.importLessonChunks).not.toHaveBeenCalled()
    expect(imported).not.toHaveBeenCalled()
  })

  it('uses source language and current lesson exclusion, then advances to Blind Listening', async () => {
    const user = userEvent.setup()
    const item = oldChunk()
    const client = memoryClient([item])
    render(
      <TrainingSession lesson={lesson} audioUrl={null} onExit={vi.fn()} memoryClient={client} devMode={false} />,
    )

    expect(await screen.findByText('来自之前的课程')).toBeInTheDocument()
    expect(client.fetchActiveRecallChunks).toHaveBeenCalledWith(
      {
        learnerId: LEARNER_ID,
        language: 'en',
        currentLessonId: 'current-lesson',
        limit: 5,
      },
      expect.any(AbortSignal),
    )
    expect(screen.queryByText('belongs to')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Reveal chunk' }))
    await user.click(screen.getByRole('button', { name: 'Got it' }))
    expect(await screen.findByRole('heading', { name: 'Blind Listening', level: 1 })).toBeInTheDocument()
  })

  it('keeps German memory isolated by requesting the lesson source language', async () => {
    const client = memoryClient()
    const germanLesson = { ...lesson, id: 'de-current', source_language: 'de' as const }
    render(
      <TrainingSession lesson={germanLesson} audioUrl={null} onExit={vi.fn()} memoryClient={client} devMode={false} />,
    )

    await screen.findByText('No earlier chunks are ready for review yet.')
    expect(client.fetchActiveRecallChunks).toHaveBeenCalledWith(
      expect.objectContaining({ language: 'de', currentLessonId: 'de-current' }),
      expect.any(AbortSignal),
    )
  })

  it('does not block training or start voice activity when import and recall are unavailable', async () => {
    const user = userEvent.setup()
    const getUserMedia = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })
    const client = memoryClient()
    vi.mocked(client.importLessonChunks).mockRejectedValue(new Error('offline'))
    vi.mocked(client.fetchActiveRecallChunks).mockRejectedValue(new Error('offline'))
    render(
      <TrainingSession lesson={lesson} audioUrl={null} onExit={vi.fn()} memoryClient={client} devMode={false} />,
    )

    expect(await screen.findByText(/Saved language is unavailable/)).toBeInTheDocument()
    expect(getUserMedia).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Complete Active Recall' }))
    expect(screen.getByRole('heading', { name: 'Blind Listening', level: 1 })).toBeInTheDocument()
  })
})
