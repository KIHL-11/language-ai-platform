import type { Lesson, SourceLanguage } from '../types/lesson'
import { API_BASE_URL } from './apiBase'

export type PracticeOutcome = 'success' | 'partial' | 'failure'

export interface ChunkMemory {
  id: string
  user_id: string
  text: string
  normalized_text: string
  meaning: string
  source_language: SourceLanguage
  source_lesson_id: string | null
  source_media_id: string | null
  source_sentence_id: number | null
  source_stage: string | null
  created_at: string
  updated_at: string
  last_practiced_at: string | null
  practice_count: number
  success_count: number
  failure_count: number
  mastery_score: number
  active: boolean
}

export interface ActiveRecallRequest {
  learnerId: string
  language: SourceLanguage
  currentLessonId: string
  limit?: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}

function isChunkMemory(value: unknown): value is ChunkMemory {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    typeof value.user_id === 'string' &&
    typeof value.text === 'string' &&
    typeof value.normalized_text === 'string' &&
    typeof value.meaning === 'string' &&
    (value.source_language === 'en' || value.source_language === 'de') &&
    (value.source_lesson_id === null ||
      typeof value.source_lesson_id === 'string') &&
    (value.source_media_id === null ||
      typeof value.source_media_id === 'string') &&
    (value.source_sentence_id === null ||
      (typeof value.source_sentence_id === 'number' &&
        Number.isInteger(value.source_sentence_id))) &&
    (value.source_stage === null || typeof value.source_stage === 'string') &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string' &&
    (value.last_practiced_at === null ||
      typeof value.last_practiced_at === 'string') &&
    Number.isInteger(value.practice_count) &&
    Number.isInteger(value.success_count) &&
    Number.isInteger(value.failure_count) &&
    typeof value.mastery_score === 'number' &&
    Number.isFinite(value.mastery_score) &&
    value.active === true
  )
}

async function requireJson(response: Response, action: string): Promise<unknown> {
  if (!response.ok) {
    throw new Error(`${action} (${response.status}).`)
  }
  try {
    return await response.json()
  } catch {
    throw new Error(`${action} returned an unexpected response.`)
  }
}

export async function importLessonChunks(
  learnerId: string,
  lesson: Lesson,
  signal?: AbortSignal,
): Promise<{ added: number; total: number }> {
  const body: {
    user_id: string
    lesson: Lesson
    source_media_id?: string
  } = { user_id: learnerId, lesson }
  if (lesson.source_url && lesson.source_url.length <= 256) {
    body.source_media_id = lesson.source_url
  }
  const response = await fetch(
    `${API_BASE_URL}/api/chunks/memory/from-lesson`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    },
  )
  const value = await requireJson(response, 'Saving lesson memory failed')
  if (
    !isRecord(value) ||
    !Number.isInteger(value.added) ||
    !Number.isInteger(value.total)
  ) {
    throw new Error('Saving lesson memory returned an unexpected response.')
  }
  return { added: value.added as number, total: value.total as number }
}

export async function fetchActiveRecallChunks(
  request: ActiveRecallRequest,
  signal?: AbortSignal,
): Promise<ChunkMemory[]> {
  const parameters = new URLSearchParams({
    user_id: request.learnerId,
    language: request.language,
    current_lesson_id: request.currentLessonId,
    limit: String(request.limit ?? 5),
  })
  const response = await fetch(
    `${API_BASE_URL}/api/chunks/memory/active-recall?${parameters}`,
    { signal },
  )
  const value = await requireJson(response, 'Loading recall memory failed')
  if (
    !isRecord(value) ||
    !Array.isArray(value.chunks) ||
    value.chunks.some((chunk) => !isChunkMemory(chunk))
  ) {
    throw new Error('Loading recall memory returned an unexpected response.')
  }
  return value.chunks
}

export async function recordChunkPractice(
  learnerId: string,
  chunkId: string,
  outcome: PracticeOutcome,
  signal?: AbortSignal,
): Promise<ChunkMemory> {
  const response = await fetch(
    `${API_BASE_URL}/api/chunks/memory/${encodeURIComponent(chunkId)}/practice`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: learnerId, outcome }),
      signal,
    },
  )
  const value = await requireJson(response, 'Saving recall result failed')
  if (!isChunkMemory(value)) {
    throw new Error('Saving recall result returned an unexpected response.')
  }
  return value
}

export interface ChunkMemoryClient {
  importLessonChunks: typeof importLessonChunks
  fetchActiveRecallChunks: typeof fetchActiveRecallChunks
  recordChunkPractice: typeof recordChunkPractice
}

export const chunkMemoryClient: ChunkMemoryClient = {
  importLessonChunks,
  fetchActiveRecallChunks,
  recordChunkPractice,
}
