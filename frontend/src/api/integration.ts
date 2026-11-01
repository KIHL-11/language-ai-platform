import { apiFetch } from '../auth/client'
import { API_BASE_URL } from './apiBase'
import type {
  IntegrationOverview,
  SpeakingSuggestionSaveResponse,
  StudyPracticeEvidence,
} from '../types/integration'
import type { SourceLanguage } from '../types/lesson'

async function json<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw Object.assign(new Error(`${action} (${response.status})`), { status: response.status })
  return response.json() as Promise<T>
}

export async function getIntegrationOverview(
  userId: string,
  language?: SourceLanguage,
): Promise<IntegrationOverview> {
  const params = new URLSearchParams({ user_id: userId })
  if (language) params.set('language', language)
  return json(await apiFetch(`${API_BASE_URL}/api/integration/overview?${params}`), 'Loading Today failed')
}

export async function getStudyPracticeEvidence(
  userId: string,
  itemId: string,
): Promise<StudyPracticeEvidence> {
  const params = new URLSearchParams({ user_id: userId })
  return json(await apiFetch(`${API_BASE_URL}/api/integration/study-items/${encodeURIComponent(itemId)}?${params}`), 'Loading practice evidence failed')
}

export async function getTargetLanguages(userId: string, sourceLanguage: SourceLanguage): Promise<string[]> {
  const params = new URLSearchParams({ user_id: userId, source_language: sourceLanguage })
  const value = await json<{ target_languages: string[] }>(await apiFetch(`${API_BASE_URL}/api/integration/target-languages?${params}`), 'Loading target languages failed')
  if (!Array.isArray(value.target_languages) || !value.target_languages.every((item) => typeof item === 'string')) {
    throw new Error('Target languages returned an unexpected response.')
  }
  return value.target_languages
}

export async function saveSpeakingSuggestion(
  sessionId: string,
  suggestionIndex: number,
  request: { user_id: string; target_language: string; meaning?: string | null; note?: string | null },
): Promise<SpeakingSuggestionSaveResponse> {
  return json(await apiFetch(
    `${API_BASE_URL}/api/integration/speaking/sessions/${encodeURIComponent(sessionId)}/suggestions/${suggestionIndex}/save`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) },
  ), 'Saving Speaking suggestion failed')
}
