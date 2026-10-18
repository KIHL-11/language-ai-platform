import { describe, expect, it, vi } from 'vitest'

import { AudioMediaController } from './mediaController'
import { activeSentenceIndex, validCustomLoop } from './timeline'
import { createYouTubeMediaController, loadYouTubeIframeApi, youtubeVideoId } from './youtube'
import type { LessonSentence } from '../types/lesson'

function audioElement() {
  const audio = document.createElement('audio')
  let currentTime = 0
  let rate = 1
  Object.defineProperty(audio, 'currentTime', { get: () => currentTime, set: (value) => { currentTime = value } })
  Object.defineProperty(audio, 'duration', { get: () => 12 })
  Object.defineProperty(audio, 'playbackRate', { get: () => rate, set: (value) => { rate = value } })
  Object.defineProperty(audio, 'readyState', { get: () => 1 })
  audio.play = vi.fn().mockResolvedValue(undefined)
  audio.pause = vi.fn()
  audio.load = vi.fn()
  return audio
}

describe('media controller', () => {
  it('controls play, pause, seek, time, duration, and rate', async () => {
    const audio = audioElement()
    const controller = new AudioMediaController(audio)

    await controller.play()
    controller.pause()
    controller.seek(4.5)

    expect(audio.play).toHaveBeenCalledOnce()
    expect(audio.pause).toHaveBeenCalledOnce()
    expect(controller.getCurrentTime()).toBe(4.5)
    expect(controller.getDuration()).toBe(12)
    expect(controller.setPlaybackRate(0.75)).toBe(0.75)
    expect(controller.getPlaybackRate()).toBe(0.75)
    expect(controller.getStatus()).toBe('ready')
  })

  it('reports provider errors and destroys without leaking listeners', () => {
    const audio = audioElement()
    const failed = vi.fn()
    const controller = new AudioMediaController(audio, failed)

    audio.dispatchEvent(new Event('error'))
    expect(failed).toHaveBeenCalledOnce()
    expect(controller.getStatus()).toBe('error')

    controller.destroy()
    audio.dispatchEvent(new Event('error'))
    expect(failed).toHaveBeenCalledOnce()
    expect(audio.pause).toHaveBeenCalledOnce()
  })

  it.each([
    ['https://www.youtube.com/watch?v=abc_123', 'abc_123'],
    ['https://youtu.be/abc_123?t=4', 'abc_123'],
    ['https://youtube.com/shorts/abc_123', 'abc_123'],
    ['https://youtube.com/embed/abc_123', 'abc_123'],
    ['https://m.youtube.com/watch?v=abc_123', 'abc_123'],
    ['https://youtube.example/watch?v=abc_123', null],
  ])('normalizes supported YouTube URL %s', (url, id) => {
    expect(youtubeVideoId(url)).toBe(id)
  })

  it('loads one shared YouTube iframe script only on explicit request', async () => {
    const playVideo = vi.fn()
    const destroy = vi.fn()
    let playerOptions: { playerVars: Record<string, number>; events: { onReady: () => void } } | null = null
    class FakeYouTubePlayer {
      constructor(_element: HTMLElement, options: typeof playerOptions) {
        playerOptions = options
        queueMicrotask(() => options?.events.onReady())
      }
      playVideo = playVideo
      pauseVideo = vi.fn()
      seekTo = vi.fn()
      getCurrentTime = () => 0
      getDuration = () => 10
      setPlaybackRate = vi.fn()
      getPlaybackRate = () => 1
      getAvailablePlaybackRates = () => [0.5, 1, 1.5]
      destroy = destroy
    }
    const first = loadYouTubeIframeApi()
    const second = loadYouTubeIframeApi()

    expect(document.querySelectorAll('#language-ai-youtube-iframe-api')).toHaveLength(1)
    expect(first).toBe(second)
    window.YT = { Player: FakeYouTubePlayer } as never
    window.onYouTubeIframeAPIReady?.()
    await expect(first).resolves.toBe(window.YT)

    const controller = await createYouTubeMediaController(document.createElement('div'), 'abc_123')
    expect((playerOptions as unknown as { playerVars: Record<string, number> }).playerVars.autoplay).toBe(0)
    expect(playVideo).not.toHaveBeenCalled()
    controller.destroy()
    expect(destroy).toHaveBeenCalledOnce()
  })
})

describe('listening timeline', () => {
  const sentences = [
    { id: 1, start: 0, end: 1 },
    { id: 2, start: 2, end: 3 },
  ] as LessonSentence[]

  it('uses start-inclusive and end-exclusive active boundaries', () => {
    expect(activeSentenceIndex(sentences, 0, -1)).toBe(0)
    expect(activeSentenceIndex(sentences, 0.999, -1)).toBe(0)
    expect(activeSentenceIndex(sentences, 2, 0)).toBe(1)
  })

  it('keeps the previous sentence during a transcript gap', () => {
    expect(activeSentenceIndex(sentences, 1.5, 0)).toBe(0)
  })

  it('validates and clamps custom loops', () => {
    expect(validCustomLoop(1, 2, 10)).toEqual({ start: 1, end: 2 })
    expect(validCustomLoop(1, 1.2, 10)).toBeNull()
    expect(validCustomLoop(9, 12, 10)).toEqual({ start: 9, end: 10 })
  })
})
