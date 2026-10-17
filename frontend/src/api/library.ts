import { API_BASE_URL } from './apiBase'
import { createPersonalSession } from '../session/personalSession'
import type { Lesson, TrainingStage } from '../types/lesson'
import type {
  LibraryItem,
  LibraryItemSummary,
  LibraryProgress,
} from '../types/library'


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isStage(value: unknown): value is TrainingStage {
  return typeof value === 'string' && [
    'active_recall',
    'blind_listening',
    'comprehension',
    'mini_dictation',
    'shadowing',
    'retell',
    'live_dialogue',
  ].includes(value)
}

function isProgress(value: unknown): value is LibraryProgress {
  return isRecord(value) && typeof value.is_training === 'boolean' &&
    Number.isInteger(value.stage_index) && Number.isInteger(value.sentence_index) &&
    Array.isArray(value.completed_stages) && value.completed_stages.every(isStage) &&
    Array.isArray(value.training_order) && value.training_order.every(isStage) &&
    typeof value.lesson_memory_imported === 'boolean'
}

function isItem(value: unknown): value is LibraryItem {
  if (!isRecord(value) || !isRecord(value.lesson) || !isRecord(value.persistent_audio)) return false
  if (!(typeof value.id === 'string' && typeof value.source_key === 'string' &&
    typeof value.created_at === 'string' && typeof value.updated_at === 'string' &&
    typeof value.lesson.id === 'string' && typeof value.lesson.title === 'string' &&
    isProgress(value.progress) &&
    (value.persistent_audio.status === 'available' || value.persistent_audio.status === 'unavailable'))) {
    return false
  }
  try {
    const session = createPersonalSession(value.lesson as unknown as Lesson)
    const progress = value.progress
    return progress.training_order.length === session.lesson.training_order.length &&
      progress.training_order.every((stage, index) => stage === session.lesson.training_order[index]) &&
      progress.stage_index >= 0 && progress.stage_index < progress.training_order.length &&
      progress.sentence_index >= 0 && (
        session.lesson.sentences.length === 0
          ? progress.sentence_index === 0
          : progress.sentence_index < session.lesson.sentences.length
      ) && progress.completed_stages.every(
        (stage, index) => progress.training_order[index] === stage && index <= progress.stage_index,
      )
  } catch {
    return false
  }
}

function isSummary(value: unknown): value is LibraryItemSummary {
  return isRecord(value) && typeof value.id === 'string' &&
    typeof value.lesson_id === 'string' && typeof value.title === 'string' &&
    typeof value.source_url === 'string' &&
    (value.source_language === 'en' || value.source_language === 'de') &&
    typeof value.target_language === 'string' &&
    (value.level === 'B1' || value.level === 'B2' || value.level === 'C1') &&
    (value.source === 'subtitles' || value.source === 'whisper') &&
    Number.isInteger(value.sentence_count) &&
    (value.duration_seconds === null || typeof value.duration_seconds === 'number') &&
    typeof value.created_at === 'string' && typeof value.updated_at === 'string' &&
    ['not_started', 'in_progress', 'complete'].includes(String(value.progress_status)) &&
    typeof value.audio_available === 'boolean'
}

async function jsonResponse(response: Response, action: string): Promise<unknown> {
  if (!response.ok) throw new Error(`${action} (${response.status}).`)
  try {
    return await response.json()
  } catch {
    throw new Error(`${action} returned an unexpected response.`)
  }
}

export async function saveLibraryItem(
  lesson: Lesson,
  progress?: LibraryProgress,
): Promise<LibraryItem> {
  const response = await fetch(`${API_BASE_URL}/api/library/items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lesson, ...(progress ? { progress } : {}) }),
  })
  const value = await jsonResponse(response, 'Saving Lesson to My Library failed')
  if (!isItem(value)) throw new Error('Saving Lesson returned an unexpected response.')
  return value
}

export async function listLibraryItems(): Promise<LibraryItemSummary[]> {
  const value = await jsonResponse(
    await fetch(`${API_BASE_URL}/api/library/items`),
    'Loading My Library failed',
  )
  if (!isRecord(value) || !Array.isArray(value.items) || !value.items.every(isSummary)) {
    throw new Error('My Library returned an unexpected response.')
  }
  return value.items
}

export async function getLibraryItem(itemId: string): Promise<LibraryItem> {
  const value = await jsonResponse(
    await fetch(`${API_BASE_URL}/api/library/items/${encodeURIComponent(itemId)}`),
    'Loading Library Lesson failed',
  )
  if (!isItem(value)) throw new Error('Library Lesson returned an unexpected response.')
  return value
}

export async function updateLibraryProgress(
  itemId: string,
  progress: LibraryProgress,
): Promise<LibraryItem> {
  const value = await jsonResponse(
    await fetch(
      `${API_BASE_URL}/api/library/items/${encodeURIComponent(itemId)}/progress`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(progress),
      },
    ),
    'Saving Library progress failed',
  )
  if (!isItem(value)) throw new Error('Library progress returned an unexpected response.')
  return value
}

export async function deleteLibraryItem(itemId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/library/items/${encodeURIComponent(itemId)}`,
    { method: 'DELETE' },
  )
  if (!response.ok) throw new Error(`Removing Library Lesson failed (${response.status}).`)
}
