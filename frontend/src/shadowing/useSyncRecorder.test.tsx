import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MediaController, MediaControllerStatus } from '../listening/mediaController'
import { maximumRecordingSeconds, supportedRecordingMimeType, useSyncRecorder } from './useSyncRecorder'

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  static supported = new Set<string>()
  static emitData = true
  static isTypeSupported(type: string) { return FakeMediaRecorder.supported.has(type) }
  state: RecordingState = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onstop: ((event: Event) => void) | null = null

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    if (options?.mimeType) this.mimeType = options.mimeType
    FakeMediaRecorder.instances.push(this)
  }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    if (FakeMediaRecorder.emitData) {
      this.ondataavailable?.({ data: new Blob(['attempt'], { type: this.mimeType }) } as BlobEvent)
    }
    this.onstop?.(new Event('stop'))
  }
}

class FakeController implements MediaController {
  play = vi.fn(async () => undefined)
  pause = vi.fn()
  seek = vi.fn()
  getCurrentTime = vi.fn(() => 0)
  getDuration = vi.fn(() => 20)
  setPlaybackRate = vi.fn((rate: number) => rate)
  getPlaybackRate = vi.fn(() => 1)
  getStatus = vi.fn((): MediaControllerStatus => 'ready')
  destroy = vi.fn()
}

function Harness({ controller }: { controller: MediaController | null }) {
  const recording = useSyncRecorder(2)
  return <>
    <p>{recording.status}</p>
    <p>{recording.countdown}</p>
    <button type="button" onClick={() => void recording.start(controller, 4)}>Start</button>
    <button type="button" onClick={recording.reset}>Reset</button>
    {recording.error && <p role="alert">{recording.error}</p>}
    {recording.blob && <p>Blob {recording.blob.size} {recording.blob.type}</p>}
    {recording.playbackUrl && <audio aria-label="attempt" src={recording.playbackUrl} />}
  </>
}

describe('sync recorder orchestration', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeMediaRecorder.instances = []
    FakeMediaRecorder.supported = new Set()
    FakeMediaRecorder.emitData = true
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:sync-attempt') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('keeps the bounded recording rule deterministic', () => {
    expect(maximumRecordingSeconds(2)).toBe(10)
    expect(maximumRecordingSeconds(9)).toBe(18)
    expect(maximumRecordingSeconds(40)).toBe(30)
  })

  it('selects a supported container deterministically and preserves the recorded MIME type', async () => {
    FakeMediaRecorder.supported = new Set(['audio/mp4', 'audio/ogg'])
    expect(supportedRecordingMimeType()).toBe('audio/mp4')
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
    })
    render(<Harness controller={new FakeController()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await act(async () => { await Promise.resolve(); await vi.advanceTimersByTimeAsync(3_250) })
    await act(async () => { await vi.advanceTimersByTimeAsync(2_750) })
    expect(FakeMediaRecorder.instances[0].mimeType).toBe('audio/mp4')
    expect(screen.getByText('recorded')).toBeInTheDocument()
    expect(screen.getByText('Blob 7 audio/mp4')).toBeInTheDocument()
  })

  it('does not create or upload an empty capture', async () => {
    FakeMediaRecorder.emitData = false
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
    })
    render(<Harness controller={new FakeController()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await act(async () => { await Promise.resolve(); await vi.advanceTimersByTimeAsync(3_250) })
    await act(async () => { await vi.advanceTimersByTimeAsync(2_750) })
    expect(screen.getByRole('alert')).toHaveTextContent('No audio was captured')
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('requests the microphone only after a click, counts down, records before reference playback, and cleans up', async () => {
    const stopTrack = vi.fn()
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream
    const getUserMedia = vi.fn().mockResolvedValue(stream)
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
    const controller = new FakeController()
    const { unmount } = render(<Harness controller={controller} />)

    expect(getUserMedia).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await act(async () => { await Promise.resolve() })
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    expect(screen.getByText('3')).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000) })
    expect(screen.getByText('Go')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(screen.getByText('recording')).toBeInTheDocument()
    expect(controller.setPlaybackRate).toHaveBeenCalledWith(1)
    expect(controller.seek).toHaveBeenCalledWith(4)
    expect(controller.play).toHaveBeenCalledOnce()

    await act(async () => { await vi.advanceTimersByTimeAsync(2_750) })
    expect(screen.getByText('recorded')).toBeInTheDocument()
    expect(screen.getByLabelText('attempt')).toHaveAttribute('src', 'blob:sync-attempt')
    expect(screen.getByText('Blob 7 audio/webm')).toBeInTheDocument()
    expect(stopTrack).toHaveBeenCalled()

    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:sync-attempt')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops tracks and timers when canceled during countdown', async () => {
    const stopTrack = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }) },
    })
    render(<Harness controller={new FakeController()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    await act(async () => { await Promise.resolve() })
    expect(stopTrack).toHaveBeenCalledOnce()
    expect(screen.getByText('idle')).toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)
  })
})
