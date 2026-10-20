import { API_BASE_URL } from './apiBase'
import type { SpeakingContext, SpeakingCorrection, SpeakingEndReason, SpeakingProviderInfo, SpeakingSession, SpeakingSetup, SpeakingStatus, SpeakingSuggestion } from '../types/speaking'

async function json<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw Object.assign(new Error(`${action} (${response.status})`), { status: response.status })
  return response.json() as Promise<T>
}

export class SpeakingApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function getSpeakingProviders(): Promise<SpeakingProviderInfo[]> {
  const value = await json<{ providers: SpeakingProviderInfo[] }>(await fetch(`${API_BASE_URL}/api/speaking/providers`), 'Loading speaking providers failed')
  return value.providers
}

export async function previewSpeakingContext(setup: Omit<SpeakingSetup, 'provider' | 'duration_seconds'>): Promise<SpeakingContext> {
  return json(await fetch(`${API_BASE_URL}/api/speaking/context`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(setup) }), 'Loading speaking targets failed')
}

export async function createSpeakingSession(setup: SpeakingSetup): Promise<{ session: SpeakingSession; context: SpeakingContext }> {
  return json(await fetch(`${API_BASE_URL}/api/speaking/sessions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(setup) }), 'Creating speaking session failed')
}

export async function listSpeakingSessions(userId: string): Promise<SpeakingSession[]> {
  const result = await json<{ sessions: SpeakingSession[] }>(await fetch(`${API_BASE_URL}/api/speaking/sessions?user_id=${encodeURIComponent(userId)}&limit=10`), 'Loading speaking history failed')
  return result.sessions
}

export async function appendSpeakingEvent(sessionId: string, userId: string, event: object): Promise<SpeakingSession> {
  return json(await fetch(`${API_BASE_URL}/api/speaking/sessions/${encodeURIComponent(sessionId)}/events?user_id=${encodeURIComponent(userId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(event) }), 'Saving speaking event failed')
}

export async function finishSpeakingSession(sessionId: string, userId: string, expected_event_count: number, status: Exclude<SpeakingStatus, 'active'>, end_reason: SpeakingEndReason): Promise<SpeakingSession> {
  return json(await fetch(`${API_BASE_URL}/api/speaking/sessions/${encodeURIComponent(sessionId)}/finish?user_id=${encodeURIComponent(userId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_event_count, status, end_reason }) }), 'Finishing speaking session failed')
}

export async function createGeminiToken(): Promise<{ value: string; model: string; expires_at: string }> {
  const response = await fetch(`${API_BASE_URL}/api/speaking/gemini/client-token`, { method: 'POST' })
  if (!response.ok) throw new SpeakingApiError('Gemini credential request failed.', response.status)
  return response.json() as Promise<{ value: string; model: string; expires_at: string }>
}

export async function executeSpeakingTool(sessionId: string, userId: string, expected_event_count: number, name: string, args: Record<string, unknown>): Promise<{ result: unknown; session: SpeakingSession | null }> {
  return json(await fetch(`${API_BASE_URL}/api/speaking/sessions/${encodeURIComponent(sessionId)}/tools`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, expected_event_count, name, arguments: args }),
  }), 'Speaking tool failed')
}

export type SpeakingStructuredEvent = { correction?: SpeakingCorrection; suggestion?: SpeakingSuggestion }
