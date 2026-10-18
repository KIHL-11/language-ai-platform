export type MediaControllerStatus = 'loading' | 'ready' | 'error'

export interface MediaController {
  play(): Promise<void>
  pause(): void
  seek(seconds: number): void
  getCurrentTime(): number
  getDuration(): number | null
  setPlaybackRate(rate: number): number
  getPlaybackRate(): number
  getStatus(): MediaControllerStatus
  destroy(): void
}

export class AudioMediaController implements MediaController {
  private status: MediaControllerStatus = 'loading'
  private readonly audio: HTMLAudioElement

  private readonly onReady = () => { this.status = 'ready' }
  private readonly onFailure: () => void

  constructor(
    audio: HTMLAudioElement,
    onError: () => void = () => undefined,
  ) {
    this.audio = audio
    this.onFailure = () => {
      this.status = 'error'
      onError()
    }
    audio.addEventListener('loadedmetadata', this.onReady)
    audio.addEventListener('canplay', this.onReady)
    audio.addEventListener('error', this.onFailure)
    if (audio.readyState > 0) this.status = 'ready'
  }

  async play(): Promise<void> {
    await this.audio.play()
  }

  pause(): void {
    this.audio.pause()
  }

  seek(seconds: number): void {
    this.audio.currentTime = Math.max(0, seconds)
  }

  getCurrentTime(): number {
    return Number.isFinite(this.audio.currentTime) ? this.audio.currentTime : 0
  }

  getDuration(): number | null {
    return Number.isFinite(this.audio.duration) ? this.audio.duration : null
  }

  setPlaybackRate(rate: number): number {
    this.audio.playbackRate = rate
    return this.audio.playbackRate
  }

  getPlaybackRate(): number {
    return this.audio.playbackRate
  }

  getStatus(): MediaControllerStatus {
    return this.status
  }

  destroy(): void {
    this.audio.pause()
    this.audio.removeEventListener('loadedmetadata', this.onReady)
    this.audio.removeEventListener('canplay', this.onReady)
    this.audio.removeEventListener('error', this.onFailure)
    this.audio.removeAttribute('src')
    this.audio.load()
  }
}

export function createAudioMediaController(
  url: string,
  onError?: () => void,
): MediaController {
  const audio = new Audio(url)
  audio.preload = 'metadata'
  return new AudioMediaController(audio, onError)
}
