import { apiFetch } from '../auth/client'
import { API_BASE_URL } from './apiBase'
import type {
  StudyCandidate,
  StudyItem,
  StudyItemInput,
  StudyKind,
} from '../types/studyBank'
import type { SourceLanguage } from '../types/lesson'


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isProvenance(value: unknown): boolean {
  if (!isRecord(value)) return false
  if (value.source_type === 'ai_text') {
    return typeof value.source_label === 'string' && typeof value.source_text === 'string' &&
      Number.isInteger(value.evidence_start) && Number(value.evidence_start) >= 0 &&
      Number.isInteger(value.evidence_end) && Number(value.evidence_end) > Number(value.evidence_start) &&
      Number(value.evidence_end) <= value.source_text.length
  }
  if (value.source_type === 'speaking') {
    return typeof value.speaking_session_id === 'string' &&
      Number.isInteger(value.suggestion_index) && Number(value.suggestion_index) >= 0 &&
      Number.isInteger(value.originating_turn) && Number(value.originating_turn) >= 1 &&
      (value.provider === 'gemini-live' || value.provider === 'openai-realtime') &&
      ['review_weak', 'media', 'custom'].includes(String(value.mode)) &&
      isNullableString(value.media_title_snapshot) && isNullableString(value.evidence_text)
  }
  return (value.source_type === 'media' || value.source_type === undefined) &&
    typeof value.library_item_id === 'string' &&
    typeof value.lesson_id === 'string' && typeof value.source_url === 'string' &&
    typeof value.media_title === 'string' && Number.isInteger(value.sentence_id) &&
    typeof value.sentence_text === 'string' && typeof value.sentence_translation === 'string' &&
    typeof value.start === 'number' && Number.isFinite(value.start) &&
    typeof value.end === 'number' && Number.isFinite(value.end) &&
    value.start >= 0 && value.end >= value.start
}

function isMediaProvenance(value: unknown): boolean {
  return isProvenance(value) && isRecord(value) && value.source_type !== 'speaking'
}

function isNullableString(value: unknown): boolean {
  return value === null || typeof value === 'string'
}

function isReviewState(value: unknown): boolean {
  if (!isRecord(value)) return false
  const counters = ['interval_seconds', 'repetition_count', 'lapse_count', 'review_count', 'again_count', 'hard_count', 'good_count', 'easy_count']
  return typeof value.due_at === 'string' &&
    counters.every((key) => Number.isInteger(value[key]) && Number(value[key]) >= 0) &&
    typeof value.ease_factor === 'number' && Number.isFinite(value.ease_factor) &&
    (value.last_reviewed_at === null || typeof value.last_reviewed_at === 'string') &&
    (value.last_rating === null || ['again', 'hard', 'good', 'easy'].includes(String(value.last_rating)))
}

export function isStudyItem(value: unknown): value is StudyItem {
  return isRecord(value) && typeof value.id === 'string' &&
    typeof value.user_id === 'string' &&
    (value.kind === 'word' || value.kind === 'chunk') &&
    (value.source_language === 'en' || value.source_language === 'de') &&
    typeof value.target_language === 'string' &&
    typeof value.text === 'string' && typeof value.normalized_text === 'string' &&
    typeof value.meaning === 'string' && typeof value.active === 'boolean' &&
    typeof value.created_at === 'string' && typeof value.updated_at === 'string' &&
    isNullableString(value.lemma) && isNullableString(value.part_of_speech) &&
    isNullableString(value.example) && isNullableString(value.usage) &&
    isNullableString(value.note) &&
    Array.isArray(value.provenance) && value.provenance.every(isProvenance) &&
    (value.revision === undefined || (Number.isInteger(value.revision) && Number(value.revision) >= 1)) &&
    isReviewState(value.review)
}

function isCandidate(value: unknown): value is StudyCandidate {
  return isRecord(value) && typeof value.candidate_id === 'string' &&
    (value.kind === 'word' || value.kind === 'chunk') &&
    (value.source_language === 'en' || value.source_language === 'de') &&
    typeof value.target_language === 'string' &&
    typeof value.text === 'string' && typeof value.normalized_text === 'string' &&
    typeof value.meaning === 'string' &&
    isNullableString(value.lemma) && isNullableString(value.part_of_speech) &&
    isNullableString(value.example) && isNullableString(value.usage) &&
    ['recommended', 'review', 'rejected'].includes(String(value.quality)) &&
    Array.isArray(value.quality_reasons) && value.quality_reasons.every((reason) => typeof reason === 'string') &&
    isMediaProvenance(value.provenance) &&
    typeof value.already_saved === 'boolean' && isNullableString(value.study_item_id)
}

async function json(response: Response, action: string): Promise<unknown> {
  if (!response.ok) throw new Error(`${action} (${response.status}).`)
  try {
    return await response.json()
  } catch {
    throw new Error(`${action} returned an unexpected response.`)
  }
}

export async function listStudyCandidates(
  libraryItemId: string,
  userId: string,
): Promise<StudyCandidate[]> {
  const params = new URLSearchParams({ user_id: userId })
  const value = await json(await apiFetch(
    `${API_BASE_URL}/api/library/items/${encodeURIComponent(libraryItemId)}/study-candidates?${params}`,
  ), 'Loading study candidates failed')
  if (!isRecord(value) || !Array.isArray(value.candidates) || !value.candidates.every(isCandidate)) {
    throw new Error('Study candidates returned an unexpected response.')
  }
  return value.candidates
}

export async function saveStudyItems(items: StudyItemInput[]): Promise<StudyItem[]> {
  const value = await json(await apiFetch(`${API_BASE_URL}/api/study-bank/items/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  }), 'Saving Words & Chunks failed')
  if (!isRecord(value) || !Array.isArray(value.items) || !value.items.every(isStudyItem)) {
    throw new Error('Saving Words & Chunks returned an unexpected response.')
  }
  return value.items
}

export type ContextualWordStatus = 'unsaved' | 'saved' | 'archived'

export interface ContextualWordStatusResult {
  status: ContextualWordStatus
  item: StudyItem | null
}

export async function getContextualWordStatus(
  libraryItemId: string,
  userId: string,
  sentenceId: number,
  word: string,
  signal?: AbortSignal,
): Promise<ContextualWordStatusResult> {
  const params = new URLSearchParams({
    user_id: userId,
    sentence_id: String(sentenceId),
    word,
  })
  const value = await json(await apiFetch(
    `${API_BASE_URL}/api/library/items/${encodeURIComponent(libraryItemId)}/study-bank/words/status?${params}`,
    { signal },
  ), 'Loading saved-word status failed')
  if (!isRecord(value) || !['unsaved', 'saved', 'archived'].includes(String(value.status)) ||
      !(value.item === null || isStudyItem(value.item))) {
    throw new Error('Saved-word status returned an unexpected response.')
  }
  return value as unknown as ContextualWordStatusResult
}

export async function saveContextualWord(
  libraryItemId: string,
  userId: string,
  sentenceId: number,
  word: string,
): Promise<{ item: StudyItem; created: boolean }> {
  const value = await json(await apiFetch(
    `${API_BASE_URL}/api/library/items/${encodeURIComponent(libraryItemId)}/study-bank/words`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, sentence_id: sentenceId, word }),
    },
  ), 'Saving word failed')
  if (!isRecord(value) || !isStudyItem(value.item) || typeof value.created !== 'boolean') {
    throw new Error('Saving word returned an unexpected response.')
  }
  return { item: value.item, created: value.created }
}

export interface StudyListFilters {
  userId: string
  kind: StudyKind
  language?: SourceLanguage
  active: boolean
  search?: string
}

export async function listStudyItems(filters: StudyListFilters): Promise<StudyItem[]> {
  const params = new URLSearchParams({
    user_id: filters.userId,
    kind: filters.kind,
    active: String(filters.active),
  })
  if (filters.language) params.set('language', filters.language)
  if (filters.search?.trim()) params.set('search', filters.search.trim())
  const value = await json(
    await apiFetch(`${API_BASE_URL}/api/study-bank/items?${params}`),
    'Loading Words & Chunks failed',
  )
  if (!isRecord(value) || !Array.isArray(value.items) || !value.items.every(isStudyItem)) {
    throw new Error('Words & Chunks returned an unexpected response.')
  }
  return value.items
}

export async function updateStudyItem(
  userId: string,
  itemId: string,
  patch: { active?: boolean; meaning?: string; note?: string; expected_revision?: number },
): Promise<StudyItem> {
  const params = new URLSearchParams({ user_id: userId })
  const value = await json(await apiFetch(
    `${API_BASE_URL}/api/study-bank/items/${encodeURIComponent(itemId)}?${params}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  ), 'Updating Study Bank item failed')
  if (!isStudyItem(value)) throw new Error('Study Bank update returned an unexpected response.')
  return value
}

export async function deleteStudyItem(userId: string, itemId: string, revision?: number): Promise<void> {
  const params = new URLSearchParams({ user_id: userId })
  if (revision) params.set('expected_revision', String(revision))
  const response = await apiFetch(
    `${API_BASE_URL}/api/study-bank/items/${encodeURIComponent(itemId)}?${params}`,
    { method: 'DELETE' },
  )
  if (!response.ok) throw new Error(`Deleting Study Bank item failed (${response.status}).`)
}

export function candidateInput(userId: string, candidate: StudyCandidate): StudyItemInput {
  return {
    user_id: userId,
    kind: candidate.kind,
    source_language: candidate.source_language,
    target_language: candidate.target_language,
    text: candidate.text,
    meaning: candidate.meaning,
    lemma: candidate.lemma,
    part_of_speech: candidate.part_of_speech,
    example: candidate.example,
    usage: candidate.usage,
    note: null,
    provenance: [candidate.provenance],
  }
}
