import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Lesson } from '../../types/lesson'
import Retell from './Retell'

const lesson: Lesson = {
  id: 'retell-lesson',
  title: 'Weather report',
  source_url: 'https://example.com/lesson',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [
    {
      id: 1,
      text: 'The weather is getting colder.',
      translation: '天气正在变冷。',
      start: 0,
      end: 2,
      keywords: [
        {
          word: 'weather',
          meaning: '天气',
          lemma: 'weather',
          pos: 'noun',
          example: 'The weather changed.',
        },
      ],
      chunks: [
        {
          text: 'getting colder',
          meaning: '正在变冷',
          usage: 'gradual change',
        },
      ],
      grammar: '',
      training: {
        blind_listening: true,
        dictation: true,
        shadowing: true,
        retell: true,
      },
      ai_status: 'ok',
    },
    {
      id: 2,
      text: 'People are wearing warmer coats.',
      translation: '人们穿上了更暖和的外套。',
      start: 2,
      end: 4,
      keywords: [],
      chunks: [],
      grammar: '',
      training: {
        blind_listening: true,
        dictation: true,
        shadowing: true,
        retell: true,
      },
      ai_status: 'ok',
    },
  ],
  training_plan: {
    active_recall: { enabled: true, count: 2 },
    blind_listening: { enabled: true },
    comprehension: { enabled: true },
    mini_dictation: { enabled: true },
    shadowing: { enabled: true },
    retell: { enabled: true },
    live_dialogue: { enabled: true },
  },
  training_order: ['retell', 'live_dialogue'],
  ai: { provider: 'mock', status: 'complete' },
}

class MockMediaRecorder {
  static stopCalls = 0
  state: RecordingState = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onstop: ((event: Event) => void) | null = null

  constructor(_stream: MediaStream) {}

  start() {
    this.state = 'recording'
  }

  stop() {
    MockMediaRecorder.stopCalls += 1
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['retell']) } as BlobEvent)
    this.onstop?.(new Event('stop'))
  }
}

function createMediaStream() {
  const stop = vi.fn()
  return {
    stream: { getTracks: () => [{ stop }] } as unknown as MediaStream,
    stop,
  }
}

function installMedia(stream: MediaStream) {
  const getUserMedia = vi.fn().mockResolvedValue(stream)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  })
  return getUserMedia
}

describe('Retell', () => {
  beforeEach(() => {
    MockMediaRecorder.stopCalls = 0
    vi.stubGlobal('MediaRecorder', MockMediaRecorder)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:retell'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('starts in Prepare without requesting a microphone or showing transcript', async () => {
    const user = userEvent.setup()
    const { stream } = createMediaStream()
    const getUserMedia = installMedia(stream)

    render(<Retell lesson={lesson} onComplete={vi.fn()} />)

    expect(screen.getByText(lesson.title)).toBeInTheDocument()
    expect(screen.getByText(lesson.sentences[0].translation)).toBeInTheDocument()
    expect(screen.queryByText(lesson.sentences[0].text)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete Retell' })).not.toBeInTheDocument()
    expect(getUserMedia).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Start Retell' }))

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(screen.getByText('Recording...')).toBeInTheDocument()
    expect(screen.getByText('00:00 / 00:45')).toBeInTheDocument()
    expect(screen.queryByText(lesson.sentences[0].text)).not.toBeInTheDocument()
  })

  it('reviews a recording, reveals reference, and completes without checkbox gating', async () => {
    const user = userEvent.setup()
    const { stream } = createMediaStream()
    installMedia(stream)
    let now = 0
    vi.spyOn(performance, 'now').mockImplementation(() => now)
    const onComplete = vi.fn()

    render(<Retell lesson={lesson} onComplete={onComplete} />)
    await user.click(screen.getByRole('button', { name: 'Start Retell' }))
    now = 36_000
    await user.click(screen.getByRole('button', { name: 'Stop' }))

    expect(screen.getByText('36.0 seconds')).toBeInTheDocument()
    expect(screen.getByText('Within the 30–45 second target.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play My Retell' })).toBeEnabled()
    expect(screen.queryByText(lesson.sentences[0].text)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Complete Retell' })).toBeDisabled()
    expect(screen.getByText(/app does not verify them/i)).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(3)
    expect(screen.getAllByRole('checkbox').every((checkbox) => !(checkbox as HTMLInputElement).checked)).toBe(true)

    await user.click(screen.getByRole('button', { name: 'Reveal Reference' }))

    expect(screen.getByText(lesson.sentences[0].text)).toBeInTheDocument()
    expect(screen.getByText(lesson.sentences[1].text)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Complete Retell' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Complete Retell' }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('Try Again removes the recording and resets preparation state', async () => {
    const user = userEvent.setup()
    const { stream } = createMediaStream()
    installMedia(stream)

    render(<Retell lesson={lesson} onComplete={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Start Retell' }))
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await user.click(screen.getByRole('button', { name: 'Reveal Reference' }))
    await user.click(screen.getAllByRole('checkbox')[0])
    await user.click(screen.getByRole('button', { name: 'Try Again' }))

    expect(screen.getByRole('button', { name: 'Start Retell' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Play My Retell' })).not.toBeInTheDocument()
    expect(screen.queryByText(lesson.sentences[0].text)).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:retell')
  })

  it('auto-stops at 45 seconds and creates a valid review recording', async () => {
    vi.useFakeTimers()
    const { stream, stop } = createMediaStream()
    installMedia(stream)

    render(<Retell lesson={lesson} onComplete={vi.fn()} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Retell' }))
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000)
    })

    expect(MockMediaRecorder.stopCalls).toBe(1)
    expect(stop).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Play My Retell' })).toBeEnabled()
    expect(screen.getByText('45.0 seconds')).toBeInTheDocument()
    expect(screen.getByText('Reached the 45-second practice limit.')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Recording stopped at the 45-second practice limit.',
    )
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cleans microphone, timer, and Blob URL resources on unmount', async () => {
    vi.useFakeTimers()
    const first = createMediaStream()
    const getUserMedia = installMedia(first.stream)
    const active = render(<Retell lesson={lesson} onComplete={vi.fn()} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Retell' }))
    })
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    active.unmount()
    expect(first.stop).toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)

    const second = createMediaStream()
    getUserMedia.mockResolvedValue(second.stream)
    const completed = render(<Retell lesson={lesson} onComplete={vi.fn()} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Retell' }))
    })
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    completed.unmount()

    expect(second.stop).toHaveBeenCalled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:retell')
    expect(vi.getTimerCount()).toBe(0)
  })
})
