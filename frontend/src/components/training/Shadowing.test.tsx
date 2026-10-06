import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LessonSentence } from '../../types/lesson'
import Shadowing from './Shadowing'

const sentence: LessonSentence = {
  id: 1,
  text: 'The weather is getting colder.',
  translation: '天气正在变冷。',
  start: 1,
  end: 3,
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
}

const nextSentence: LessonSentence = {
  ...sentence,
  id: 2,
  text: 'The next sentence starts clean.',
  start: 3,
  end: 5,
}

class MockMediaRecorder {
  static instances: MockMediaRecorder[] = []
  static stopCalls = 0
  state: RecordingState = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onstop: ((event: Event) => void) | null = null

  constructor(_stream: MediaStream) {
    MockMediaRecorder.instances.push(this)
  }

  start() {
    this.state = 'recording'
  }

  stop() {
    MockMediaRecorder.stopCalls += 1
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio']) } as BlobEvent)
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

function renderShadowing(sentenceValue = sentence) {
  return render(
    <Shadowing
      sentence={sentenceValue}
      sentenceIndex={0}
      sentenceCount={2}
      audioUrl={null}
      onNext={vi.fn()}
    />,
  )
}

describe('Shadowing', () => {
  beforeEach(() => {
    MockMediaRecorder.instances = []
    MockMediaRecorder.stopCalls = 0
    vi.stubGlobal('MediaRecorder', MockMediaRecorder)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:recording'),
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

  it('requests permission only after Start Recording and handles denial', async () => {
    const user = userEvent.setup()
    const getUserMedia = vi.fn().mockRejectedValue(
      new DOMException('denied', 'NotAllowedError'),
    )
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })

    renderShadowing()

    expect(getUserMedia).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Start Recording' }))

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Microphone permission was denied',
    )
  })

  it('records, enables playback and progression, then retries cleanly', async () => {
    const user = userEvent.setup()
    const { stream, stop } = createMediaStream()
    const getUserMedia = vi.fn().mockResolvedValue(stream)
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })
    let now = 1_000
    vi.spyOn(performance, 'now').mockImplementation(() => now)
    const play = vi
      .spyOn(HTMLMediaElement.prototype, 'play')
      .mockResolvedValue(undefined)

    renderShadowing()

    const next = screen.getByRole('button', { name: 'Next sentence' })
    expect(next).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Start Recording' }))
    expect(screen.getByText('Recording')).toBeInTheDocument()
    expect(screen.getByText('00:00 / 00:10')).toBeInTheDocument()

    now = 3_100
    await user.click(screen.getByRole('button', { name: 'Stop' }))

    expect(stop).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Play My Recording' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeEnabled()
    expect(screen.getByText('Timing Match')).toBeInTheDocument()
    expect(next).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Play My Recording' }))
    expect(play).toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Try Again' }))
    expect(screen.getByRole('button', { name: 'Start Recording' })).toBeEnabled()
    expect(next).toBeDisabled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:recording')
  })

  it('clears the previous recording when the sentence changes', async () => {
    const user = userEvent.setup()
    const { stream } = createMediaStream()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    })
    const { rerender } = renderShadowing()

    await user.click(screen.getByRole('button', { name: 'Start Recording' }))
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(screen.getByRole('button', { name: 'Play My Recording' })).toBeInTheDocument()

    rerender(
      <Shadowing
        sentence={nextSentence}
        sentenceIndex={1}
        sentenceCount={2}
        audioUrl={null}
        onNext={vi.fn()}
      />,
    )

    expect(screen.getByText(nextSentence.text)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Play My Recording' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Recording' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Complete stage' })).toBeDisabled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:recording')
  })

  it('stops an active microphone track when unmounted', async () => {
    vi.useFakeTimers()
    const { stream, stop } = createMediaStream()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    })
    const { unmount } = renderShadowing()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Recording' }))
    })
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    unmount()

    expect(stop).toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('automatically stops at the limit and completes the attempt', async () => {
    vi.useFakeTimers()
    const { stream, stop } = createMediaStream()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    })

    renderShadowing()
    const next = screen.getByRole('button', { name: 'Next sentence' })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Recording' }))
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(MockMediaRecorder.stopCalls).toBe(1)
    expect(stop).toHaveBeenCalled()
    expect(screen.getByText('Recording ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play My Recording' })).toBeEnabled()
    expect(screen.getByText('10.00s')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Recording automatically stopped',
    )
    expect(next).toBeEnabled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('manual stop cancels auto-stop and retry leaves no timer', async () => {
    vi.useFakeTimers()
    const { stream } = createMediaStream()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    })

    renderShadowing()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start Recording' }))
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))

    expect(MockMediaRecorder.stopCalls).toBe(1)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000)
    })
    expect(MockMediaRecorder.stopCalls).toBe(1)

    fireEvent.click(screen.getByRole('button', { name: 'Try Again' }))
    expect(vi.getTimerCount()).toBe(0)
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeDisabled()
  })
})
