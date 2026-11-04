import { apiFetch } from '../auth/client'
import { API_BASE_URL } from './apiBase'
import type { SourceLanguage } from '../types/lesson'
import type { AiChunkBatch, CandidateDecisionResult, CandidateEdit } from '../types/aiChunks'

async function responseJson<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw new Error(`${action} (${response.status})`)
  return await response.json() as T
}

function key(): string {
  return crypto.randomUUID().replaceAll('-', '')
}

export async function generateChunkSuggestions(
  userId: string,
  language: SourceLanguage,
  text: string,
): Promise<AiChunkBatch> {
  return responseJson<AiChunkBatch>(await apiFetch(`${API_BASE_URL}/api/ai/chunk-suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      source_language: language,
      source: { source_type: 'manual_text', text, label: 'Manual learning text' },
      max_candidates: 5,
      idempotency_key: key(),
    }),
  }), 'Generating Chunk candidates failed')
}

// B-3: candidates from sentences of one of the owner's Cloud Library lessons.
// The server resolves the lesson under the session owner and records full
// provenance (item, lesson, sentence, URL, timing) on every candidate.
export async function generateLibraryChunkSuggestions(
  userId: string,
  language: SourceLanguage,
  libraryItemId: string,
  sentenceIds: number[],
): Promise<AiChunkBatch> {
  return responseJson<AiChunkBatch>(await apiFetch(`${API_BASE_URL}/api/ai/chunk-suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      source_language: language,
      source: { source_type: 'library_passage', library_item_id: libraryItemId, sentence_ids: sentenceIds },
      max_candidates: 8,
      idempotency_key: key(),
    }),
  }), 'Generating Chunk candidates failed')
}

export async function getChunkSuggestions(userId: string, batchId: string): Promise<AiChunkBatch> {
  const params = new URLSearchParams({ user_id: userId })
  return responseJson<AiChunkBatch>(await apiFetch(
    `${API_BASE_URL}/api/ai/chunk-suggestions/${encodeURIComponent(batchId)}?${params}`,
  ), 'Loading Chunk candidates failed')
}

export async function decideChunkSuggestion(
  userId: string,
  batchId: string,
  candidateId: string,
  expectedVersion: number,
  // 'edit' saves the changes and keeps the candidate proposed.
  action: 'accept' | 'edit_and_accept' | 'edit' | 'reject',
  edit?: CandidateEdit,
): Promise<CandidateDecisionResult> {
  return responseJson<CandidateDecisionResult>(await apiFetch(
    `${API_BASE_URL}/api/ai/chunk-suggestions/${encodeURIComponent(batchId)}/decisions`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        candidate_id: candidateId,
        expected_version: expectedVersion,
        action,
        idempotency_key: key(),
        ...(edit ? { edit } : {}),
      }),
    },
  ), 'Saving Chunk decision failed')
}
