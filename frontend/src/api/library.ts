import { API_BASE_URL } from './apiBase'
import { createPersonalSession } from '../session/personalSession'
import type { Lesson, TrainingStage } from '../types/lesson'
import type {
  LibraryItem,
  LibraryItemSummary,
  ListeningProgress,
  LibraryProgress,
  ShadowingAttemptResponse,
  ShadowingMetrics,
  ShadowingProgress,
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

function isListeningProgress(value: unknown): value is ListeningProgress {
  return isRecord(value) && typeof value.last_position_seconds === 'number' &&
    Number.isFinite(value.last_position_seconds) && value.last_position_seconds >= 0 &&
    (value.current_sentence_id === null || Number.isInteger(value.current_sentence_id)) &&
    [0.5, 0.75, 1, 1.25].includes(Number(value.playback_rate)) &&
    typeof value.source_visible === 'boolean' &&
    typeof value.translation_visible === 'boolean' &&
    ['listen', 'blind', 'dictation'].includes(String(value.mode))
}

function isShadowingMetrics(value: unknown): value is ShadowingMetrics {
  const nullableNumber = (candidate: unknown) => candidate === null || typeof candidate === 'number'
  return isRecord(value) && typeof value.recognized_text === 'string' &&
    typeof value.target_word_count === 'number' && typeof value.recognized_word_count === 'number' &&
    typeof value.transcript_match_percent === 'number' &&
    typeof value.matched_count === 'number' && typeof value.missed_count === 'number' &&
    typeof value.extra_count === 'number' && typeof value.substituted_count === 'number' &&
    nullableNumber(value.reference_speech_duration_ms) && nullableNumber(value.attempt_speech_duration_ms) &&
    nullableNumber(value.duration_delta_ms) && nullableNumber(value.duration_delta_ratio) &&
    nullableNumber(value.start_delay_ms) && nullableNumber(value.word_timing_mae_ms) &&
    nullableNumber(value.pause_mae_ms) && typeof value.aligned_timed_word_count === 'number' &&
    typeof value.timing_reference_available === 'boolean' &&
    (value.timing_unavailable_reason === null || typeof value.timing_unavailable_reason === 'string') &&
    typeof value.sync_target_met === 'boolean' && Array.isArray(value.aligned_words) &&
    value.aligned_words.every((word) => isRecord(word) &&
      ['matched', 'missed', 'extra', 'substituted'].includes(String(word.status)) &&
      (word.target === null || typeof word.target === 'string') &&
      (word.recognized === null || typeof word.recognized === 'string'))
}

function isSentenceShadowingProgress(sentence: unknown): boolean {
  const nullableNumber = (candidate: unknown) => candidate === null || typeof candidate === 'number'
  return isRecord(sentence) && Number.isInteger(sentence.attempt_count) &&
    Number.isInteger(sentence.target_hit_count) && Number.isInteger(sentence.consecutive_target_hits) &&
    (sentence.last_attempt_at === null || typeof sentence.last_attempt_at === 'string') &&
    (sentence.last_metrics === null || isShadowingMetrics(sentence.last_metrics)) &&
    nullableNumber(sentence.best_transcript_match_percent) &&
    nullableNumber(sentence.best_duration_delta_ratio) &&
    nullableNumber(sentence.best_abs_start_delay_ms) &&
    nullableNumber(sentence.best_word_timing_mae_ms) && nullableNumber(sentence.best_pause_mae_ms)
}

function isShadowingProgress(value: unknown): value is ShadowingProgress {
  return isRecord(value) &&
    (value.last_sentence_id === null || Number.isInteger(value.last_sentence_id)) &&
    (value.last_practiced_at === null || typeof value.last_practiced_at === 'string') &&
    isRecord(value.sentences) && Object.values(value.sentences).every(isSentenceShadowingProgress)
}

function isItem(value: unknown): value is LibraryItem {
  if (!isRecord(value) || !isRecord(value.lesson) || !isRecord(value.persistent_audio)) return false
  if (!(typeof value.id === 'string' && typeof value.source_key === 'string' &&
    typeof value.created_at === 'string' && typeof value.updated_at === 'string' &&
    typeof value.lesson.id === 'string' && typeof value.lesson.title === 'string' &&
    isProgress(value.progress) && isListeningProgress(value.listening_progress) &&
    isShadowingProgress(value.shadowing_progress) &&
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
    typeof value.audio_available === 'boolean' &&
    typeof value.last_position_seconds === 'number' &&
    Number.isFinite(value.last_position_seconds) && value.last_position_seconds >= 0 &&
    typeof value.has_listening_progress === 'boolean' &&
    Number.isInteger(value.shadowing_attempt_count) &&
    Number.isInteger(value.shadowing_target_sentence_count) &&
    Number.isInteger(value.shadowing_stable_sentence_count) &&
    (value.shadowing_last_practiced_at === null || typeof value.shadowing_last_practiced_at === 'string')
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

export async function updateListeningProgress(
  itemId: string,
  progress: ListeningProgress,
): Promise<LibraryItem> {
  const value = await jsonResponse(
    await fetch(
      `${API_BASE_URL}/api/library/items/${encodeURIComponent(itemId)}/listening-progress`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(progress),
      },
    ),
    'Saving listening progress failed',
  )
  if (!isItem(value)) throw new Error('Listening progress returned an unexpected response.')
  return value
}

export async function deleteLibraryItem(itemId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/library/items/${encodeURIComponent(itemId)}`,
    { method: 'DELETE' },
  )
  if (!response.ok) throw new Error(`Removing Library Lesson failed (${response.status}).`)
}


export class ShadowingApiError extends Error {
  readonly status: number
  readonly detail: string | null

  constructor(message: string, status: number, detail: string | null = null) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function shadowingAudioFilename(type: string): string {
  const normalized = type.toLowerCase().split(';', 1)[0]
  if (normalized === 'audio/mp4' || normalized === 'audio/x-m4a' || normalized === 'audio/aac') return 'shadowing-attempt.m4a'
  if (normalized === 'audio/ogg') return 'shadowing-attempt.ogg'
  if (normalized === 'audio/wav' || normalized === 'audio/x-wav') return 'shadowing-attempt.wav'
  if (normalized === 'audio/mpeg') return 'shadowing-attempt.mp3'
  return 'shadowing-attempt.webm'
}

export async function submitShadowingAttempt(
  itemId: string,
  sentenceId: number,
  expectedAttemptCount: number,
  audio: Blob,
  recordingDuration: number,
): Promise<ShadowingAttemptResponse> {
  const form = new FormData()
  form.set('audio', audio, shadowingAudioFilename(audio.type))
  form.set('sentence_id', String(sentenceId))
  form.set('expected_attempt_count', String(expectedAttemptCount))
  form.set('client_recording_duration', String(recordingDuration))
  let response: Response
  try {
    response = await fetch(
      `${API_BASE_URL}/api/library/items/${encodeURIComponent(itemId)}/shadowing/attempt`,
      { method: 'POST', body: form },
    )
  } catch {
    throw new ShadowingApiError('Recording upload failed.', 0)
  }
  if (!response.ok) {
    let detail: string | null = null
    try {
      const body: unknown = await response.json()
      if (isRecord(body) && typeof body.detail === 'string') detail = body.detail
    } catch {
      // Preserve the status-based diagnostic when the server did not return JSON.
    }
    throw new ShadowingApiError(`Shadowing analysis failed (${response.status}).`, response.status, detail)
  }
  const value = await jsonResponse(response, 'Shadowing analysis failed')
  if (!isRecord(value) || !Number.isInteger(value.sentence_id) ||
    !isSentenceShadowingProgress(value.progress) || !isShadowingMetrics(value.metrics) ||
    typeof value.sync_target_met !== 'boolean' || typeof value.stable_sync_achieved !== 'boolean') {
    throw new Error('Shadowing analysis returned an unexpected response.')
  }
  return value as unknown as ShadowingAttemptResponse
}
