import { API_BASE_URL } from '../api/apiBase'
import type { LibraryItem } from '../types/library'
import { createAudioMediaController, type MediaController } from './mediaController'
import { createYouTubeMediaController, youtubeVideoId } from './youtube'

export type ListeningMediaKind = 'youtube' | 'audio' | 'transcript'

export interface ListeningControllerResult {
  controller: MediaController | null
  kind: ListeningMediaKind
  message: string | null
}

export type ListeningControllerFactory = (
  item: LibraryItem,
  host: HTMLElement,
  onProviderFallback: (result: ListeningControllerResult) => void,
) => Promise<ListeningControllerResult>

export async function createListeningController(
  item: LibraryItem,
  host: HTMLElement,
  onProviderFallback: (result: ListeningControllerResult) => void,
): Promise<ListeningControllerResult> {
  const transcriptFallback = (message: string): ListeningControllerResult => ({
    controller: null,
    kind: 'transcript',
    message,
  })
  const audioFallback = (message: string): ListeningControllerResult => {
    if (item.persistent_audio.status !== 'available' || !item.lesson.audio_url) {
      return transcriptFallback('Video unavailable. Transcript-only mode is active.')
    }
    return {
      controller: createAudioMediaController(
        `${API_BASE_URL}${item.lesson.audio_url}`,
        () => onProviderFallback(transcriptFallback(
          'Saved audio could not be played. Transcript-only mode is active.',
        )),
      ),
      kind: 'audio',
      message,
    }
  }
  const videoId = youtubeVideoId(item.lesson.source_url)
  if (videoId) {
    try {
      return {
        controller: await createYouTubeMediaController(
          host,
          videoId,
          () => onProviderFallback(audioFallback('Video unavailable. Using saved audio.')),
        ),
        kind: 'youtube',
        message: null,
      }
    } catch {
      return audioFallback('Video unavailable. Using saved audio.')
    }
  }
  if (item.persistent_audio.status === 'available' && item.lesson.audio_url) {
    return audioFallback('Embedded video is unsupported for this source. Using saved audio.')
  }
  return transcriptFallback('No playable media is available. Transcript-only mode is active.')
}
