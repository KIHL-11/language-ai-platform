import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Lesson } from '../types/lesson'
import {
  fetchActiveRecallChunks,
  importLessonChunks,
  recordChunkPractice,
  type ChunkMemory,
} from './chunkMemory'


const lesson: Lesson = {
  id: 'lesson-current',
  title: 'Current lesson',
  source_url: 'https://www.youtube.com/watch?v=current',
  source_language: 'de',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [],
  training_order: [
    'active_recall',
    'blind_listening',
    'comprehension',
    'mini_dictation',
    'shadowing',
    'retell',
    'live_dialogue',
  ],
  training_plan: {
    active_recall: { enabled: true, count: 5 },
    blind_listening: { enabled: true },
    comprehension: { enabled: true },
    mini_dictation: { enabled: true },
    shadowing: { enabled: true },
    retell: { enabled: true },
    live_dialogue: { enabled: true },
  },
  ai: { provider: 'mock', status: 'complete' },
}

function chunk(id: string, text: string): ChunkMemory {
  return {
    id,
    user_id: 'learner-id',
    text,
    normalized_text: text.toLocaleLowerCase(),
    meaning: `${text} meaning`,
    source_language: 'de',
    source_lesson_id: 'lesson-old',
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

const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

describe('chunk memory API client', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('imports the full lesson with learner and media provenance', async () => {
    fetchMock.mockResolvedValue(response({ added: 2, total: 8 }))

    await importLessonChunks('learner-id', lesson)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/chunks\/memory\/from-lesson$/)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      user_id: 'learner-id',
      lesson,
      source_media_id: lesson.source_url,
    })
  })

  it('requests five backend-ranked chunks with language and current lesson exclusion', async () => {
    const first = chunk('first', 'Zuerst')
    const second = chunk('second', 'Danach')
    fetchMock.mockResolvedValue(response({ chunks: [first, second] }))
    const controller = new AbortController()

    const chunks = await fetchActiveRecallChunks(
      {
        learnerId: 'learner-id',
        language: 'de',
        currentLessonId: lesson.id,
        limit: 5,
      },
      controller.signal,
    )

    const url = new URL(fetchMock.mock.calls[0][0])
    expect(url.pathname).toBe('/api/chunks/memory/active-recall')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      user_id: 'learner-id',
      language: 'de',
      current_lesson_id: lesson.id,
      limit: '5',
    })
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal)
    expect(chunks).toEqual([first, second])
  })

  it('submits the exact backend practice outcome and returns authoritative state', async () => {
    const updated = {
      ...chunk('chunk/id', 'Erinnern'),
      practice_count: 1,
      success_count: 1,
      mastery_score: 0.1,
    }
    fetchMock.mockResolvedValue(response(updated))

    const result = await recordChunkPractice(
      'learner-id',
      'chunk/id',
      'success',
    )

    expect(fetchMock.mock.calls[0][0]).toMatch(
      /\/api\/chunks\/memory\/chunk%2Fid\/practice$/,
    )
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      user_id: 'learner-id',
      outcome: 'success',
    })
    expect(result).toEqual(updated)
  })

  it('accepts a curated recall result even if its legacy memory active flag is false', async () => {
    const archived = { ...chunk('archived', 'Alt'), active: false }
    fetchMock.mockResolvedValue(
      response({ chunks: [archived] }),
    )

    await expect(fetchActiveRecallChunks({
      learnerId: 'learner-id',
      language: 'de',
      currentLessonId: lesson.id,
      limit: 5,
    })).resolves.toEqual([archived])
  })
})
