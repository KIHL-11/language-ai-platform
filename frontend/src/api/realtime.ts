import { API_BASE_URL } from './lesson'

export interface RealtimeClientCredential {
  value: string
  expires_at: number | null
  model: string
  voice: string
}

function errorDetail(body: unknown): string | null {
  if (!body || typeof body !== 'object') {
    return null
  }
  const detail = (body as { detail?: unknown }).detail
  return typeof detail === 'string' ? detail : null
}

export async function createRealtimeClientCredential(): Promise<RealtimeClientCredential> {
  const response = await fetch(`${API_BASE_URL}/api/realtime/client-secret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })

  if (!response.ok) {
    let detail: string | null = null
    try {
      detail = errorDetail(await response.json())
    } catch {
      // The HTTP status still gives the user a useful failure reason.
    }
    throw new Error(detail || `Realtime credential request failed (${response.status}).`)
  }

  return response.json() as Promise<RealtimeClientCredential>
}
