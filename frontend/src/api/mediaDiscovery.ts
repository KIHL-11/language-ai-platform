import { API_BASE_URL } from './lesson'
import type { MediaCandidate, MediaDiscoveryRequest } from '../types/mediaDiscovery'

export async function discoverMedia(request: MediaDiscoveryRequest, signal?: AbortSignal): Promise<MediaCandidate[]> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/api/media/discover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    })
  } catch {
    throw new Error('Video search failed. Please try again.')
  }
  if (!response.ok) {
    throw new Error(response.status === 502
      ? 'Video search is temporarily unavailable. Please try again later.'
      : response.status === 422
        ? 'Check your search topic, duration range, and number of results.'
        : 'Video search failed. Please try again.')
  }
  try {
    const candidates: MediaCandidate[] = await response.json()
    if (!Array.isArray(candidates) || candidates.some(candidate =>
      !candidate || typeof candidate.provider_id !== 'string' || typeof candidate.url !== 'string' ||
      typeof candidate.title !== 'string' || (candidate.channel !== null && typeof candidate.channel !== 'string') ||
      (candidate.thumbnail !== null && typeof candidate.thumbnail !== 'string') ||
      !Number.isFinite(candidate.duration_seconds) || candidate.duration_seconds < 0 ||
      !Array.isArray(candidate.subtitle_tracks) || candidate.subtitle_tracks.some(track =>
        !track || typeof track.language !== 'string' || typeof track.has_human !== 'boolean' || typeof track.has_automatic !== 'boolean') ||
      !candidate.suitability || !Number.isFinite(candidate.suitability.total) ||
      !Array.isArray(candidate.suitability.reasons) || candidate.suitability.reasons.some(reason => typeof reason !== 'string'),
    )) throw new Error('Invalid response')
    return candidates
  } catch {
    throw new Error('Video search returned an unexpected response. Please try again.')
  }
}

export function safeMediaUrl(value: string | null, youtubeOnly = false): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return null
    if (youtubeOnly && !['www.youtube.com', 'youtube.com', 'youtu.be'].includes(url.hostname)) return null
    return value
  } catch {
    return null
  }
}
