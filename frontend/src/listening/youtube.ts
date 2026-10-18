import type { MediaController, MediaControllerStatus } from './mediaController'

const YOUTUBE_SCRIPT_ID = 'language-ai-youtube-iframe-api'
const YOUTUBE_ID = /^[A-Za-z0-9_-]{6,64}$/

interface YouTubePlayer {
  playVideo(): void
  pauseVideo(): void
  seekTo(seconds: number, allowSeekAhead: boolean): void
  getCurrentTime(): number
  getDuration(): number
  setPlaybackRate(rate: number): void
  getPlaybackRate(): number
  getAvailablePlaybackRates(): number[]
  destroy(): void
}

interface YouTubeNamespace {
  Player: new (
    element: HTMLElement,
    options: {
      videoId: string
      playerVars: Record<string, number>
      events: {
        onReady: () => void
        onError: () => void
      }
    },
  ) => YouTubePlayer
}

declare global {
  interface Window {
    YT?: YouTubeNamespace
    onYouTubeIframeAPIReady?: () => void
  }
}

let iframeApiPromise: Promise<YouTubeNamespace> | null = null

export function youtubeVideoId(sourceUrl: string): string | null {
  try {
    const url = new URL(sourceUrl)
    const host = url.hostname.toLowerCase().replace(/^www\./, '')
    let id: string | null = null
    if (host === 'youtu.be') {
      id = url.pathname.split('/').filter(Boolean)[0] ?? null
    } else if (host === 'youtube.com' || host === 'm.youtube.com') {
      if (url.pathname === '/watch') id = url.searchParams.get('v')
      if (url.pathname.startsWith('/shorts/') || url.pathname.startsWith('/embed/')) {
        id = url.pathname.split('/').filter(Boolean)[1] ?? null
      }
    }
    return id && YOUTUBE_ID.test(id) ? id : null
  } catch {
    return null
  }
}

export function loadYouTubeIframeApi(): Promise<YouTubeNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (iframeApiPromise) return iframeApiPromise
  iframeApiPromise = new Promise((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady
    const fail = (message: string) => {
      window.clearTimeout(timeout)
      document.getElementById(YOUTUBE_SCRIPT_ID)?.remove()
      window.onYouTubeIframeAPIReady = previous
      reject(new Error(message))
    }
    const timeout = window.setTimeout(
      () => fail('YouTube player API timed out.'),
      15_000,
    )
    window.onYouTubeIframeAPIReady = () => {
      previous?.()
      window.clearTimeout(timeout)
      if (window.YT?.Player) resolve(window.YT)
      else fail('YouTube player API did not initialize.')
    }
    const existing = document.getElementById(YOUTUBE_SCRIPT_ID)
    if (existing) return
    const script = document.createElement('script')
    script.id = YOUTUBE_SCRIPT_ID
    script.src = 'https://www.youtube.com/iframe_api'
    script.async = true
    script.onerror = () => {
      fail('YouTube player API could not be loaded.')
    }
    document.head.append(script)
  })
  iframeApiPromise.catch(() => { iframeApiPromise = null })
  return iframeApiPromise
}

class YouTubeMediaController implements MediaController {
  private status: MediaControllerStatus = 'ready'
  private readonly player: YouTubePlayer

  constructor(player: YouTubePlayer) { this.player = player }

  async play(): Promise<void> { this.player.playVideo() }
  pause(): void { this.player.pauseVideo() }
  seek(seconds: number): void { this.player.seekTo(Math.max(0, seconds), true) }
  getCurrentTime(): number { return this.player.getCurrentTime() || 0 }
  getDuration(): number | null {
    const duration = this.player.getDuration()
    return Number.isFinite(duration) && duration > 0 ? duration : null
  }
  setPlaybackRate(rate: number): number {
    const available = this.player.getAvailablePlaybackRates()
    const selected = available.length > 0
      ? available.reduce((nearest, value) =>
        Math.abs(value - rate) < Math.abs(nearest - rate) ? value : nearest,
      )
      : this.player.getPlaybackRate()
    this.player.setPlaybackRate(selected)
    return this.player.getPlaybackRate()
  }
  getPlaybackRate(): number { return this.player.getPlaybackRate() }
  getStatus(): MediaControllerStatus { return this.status }
  markError(): void { this.status = 'error' }
  destroy(): void {
    this.status = 'error'
    this.player.destroy()
  }
}

export async function createYouTubeMediaController(
  host: HTMLElement,
  videoId: string,
  onRuntimeError: () => void = () => undefined,
): Promise<MediaController> {
  const youtube = await loadYouTubeIframeApi()
  return await new Promise((resolve, reject) => {
    let player: YouTubePlayer
    let readyController: YouTubeMediaController | null = null
    const timeout = window.setTimeout(() => {
      player?.destroy()
      reject(new Error('YouTube player timed out.'))
    }, 15_000)
    player = new youtube.Player(host, {
      videoId,
      playerVars: { autoplay: 0, playsinline: 1, rel: 0 },
      events: {
        onReady: () => {
          window.clearTimeout(timeout)
          readyController = new YouTubeMediaController(player)
          resolve(readyController)
        },
        onError: () => {
          window.clearTimeout(timeout)
          if (readyController) {
            readyController.markError()
            onRuntimeError()
          } else {
            player.destroy()
            reject(new Error('YouTube video is unavailable.'))
          }
        },
      },
    })
  })
}
