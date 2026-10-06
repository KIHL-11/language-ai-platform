import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Lesson } from '../types/lesson'
import TrainingSession from './TrainingSession'

const lesson: Lesson = {
  id: 'retell-flow',
  title: 'Me at the zoo',
  source_url: 'https://www.youtube.com/watch?v=jNQXAC9IVRw',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [
    {
      id: 1,
      text: 'Here we are in front of the elephants.',
      translation: '我们现在就在大象前面。',
      start: 0,
      end: 2,
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
    active_recall: { enabled: false, count: 0 },
    blind_listening: { enabled: false },
    comprehension: { enabled: false },
    mini_dictation: { enabled: false },
    shadowing: { enabled: false },
    retell: { enabled: true },
    live_dialogue: { enabled: true },
  },
  training_order: ['retell', 'live_dialogue'],
  ai: { provider: 'mock', status: 'complete' },
}

class MockMediaRecorder {
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
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['retell']) } as BlobEvent)
    this.onstop?.(new Event('stop'))
  }
}

describe('TrainingSession Retell flow', () => {
  beforeEach(() => {
    vi.stubGlobal('MediaRecorder', MockMediaRecorder)
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        } as unknown as MediaStream),
      },
    })
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:retell-flow'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('completes lesson-level Retell and advances to Live Dialogue intro', async () => {
    const user = userEvent.setup()
    render(
      <TrainingSession lesson={lesson} audioUrl={null} onExit={vi.fn()} />,
    )

    expect(screen.getByRole('heading', { name: 'Retell', level: 1 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Previous sentence' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Start Retell' }))
    expect(screen.getByText('Recording...')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(screen.getByRole('button', { name: 'Play My Retell' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reveal Reference' }))
    await user.click(screen.getByRole('button', { name: 'Complete Retell' }))

    expect(screen.getByRole('heading', { name: 'Live Dialogue', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toBeInTheDocument()
  })

  it('completes the final dialogue stage and shows session completion actions', async () => {
    const user = userEvent.setup()
    const onExit = vi.fn()
    const liveDialogueLesson: Lesson = {
      ...lesson,
      id: 'live-dialogue-only',
      training_order: ['live_dialogue'],
    }
    render(<TrainingSession lesson={liveDialogueLesson} audioUrl={null} onExit={onExit} />)

    await user.click(screen.getByRole('button', { name: 'Start Conversation' }))
    for (const response of ['First response', 'Second response', 'Third response']) {
      await user.type(screen.getByLabelText('Your response'), response)
      await user.click(screen.getByRole('button', { name: 'Send' }))
    }
    await user.click(screen.getByRole('button', { name: 'Complete Live Dialogue' }))

    expect(screen.getByRole('heading', { name: 'Training Complete' })).toBeInTheDocument()
    expect(screen.getByText('Stages completed: 1 / 1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Review Lesson' }))
    expect(onExit).toHaveBeenCalledTimes(1)
  })
})
