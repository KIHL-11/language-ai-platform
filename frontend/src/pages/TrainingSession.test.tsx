import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TRAINING_STAGE_LABELS } from '../training/trainingMachine'
import type { Lesson, TrainingStage } from '../types/lesson'
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
    window.history.replaceState({}, '', '/')
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
    window.history.replaceState({}, '', '/')
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

const allStageLesson: Lesson = {
  ...lesson,
  id: 'all-stage-dev-jump',
  sentences: [
    lesson.sentences[0],
    {
      ...lesson.sentences[0],
      id: 2,
      text: 'The elephants have really, really, really long trunks.',
      translation: '这些大象有非常非常长的鼻子。',
      start: 2,
      end: 4,
    },
  ],
  training_order: [
    'active_recall',
    'blind_listening',
    'comprehension',
    'mini_dictation',
    'shadowing',
    'retell',
    'live_dialogue',
  ],
}

const stageCases: Array<[TrainingStage, string]> = Object.entries(
  TRAINING_STAGE_LABELS,
) as Array<[TrainingStage, string]>

describe('TrainingSession development stage jump', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it.each(stageCases)('opens the %s stage from the query parameter', (stage, label) => {
    window.history.replaceState({}, '', `/?stage=${stage}`)

    render(
      <TrainingSession
        lesson={allStageLesson}
        audioUrl={null}
        onExit={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading', { name: label, level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(`DEV override: ${label}`)
  })

  it('uses normal training start when no stage parameter is present', () => {
    render(
      <TrainingSession lesson={lesson} audioUrl={null} onExit={vi.fn()} />,
    )

    expect(screen.getByRole('heading', { name: 'Retell', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('DEV stage override: None')
  })

  it('ignores an invalid stage parameter', () => {
    window.history.replaceState({}, '', '/?stage=invalid')

    render(
      <TrainingSession lesson={lesson} audioUrl={null} onExit={vi.fn()} />,
    )

    expect(screen.getByRole('heading', { name: 'Retell', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('DEV stage override: None')
  })

  it('resets sentence-level overrides to sentence zero instead of reusing stale progress', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', '/?stage=blind_listening')
    render(
      <TrainingSession
        lesson={allStageLesson}
        audioUrl={null}
        onExit={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Reveal transcript' }))
    await user.click(screen.getByRole('button', { name: 'Next sentence' }))
    expect(screen.getAllByText('Sentence 2 of 2').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: 'Shadowing' }))

    expect(screen.getAllByText('Sentence 1 of 2').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Previous sentence' })).toBeDisabled()
  })

  it.each([
    ['retell', 'Retell'],
    ['live_dialogue', 'Live Dialogue'],
  ] as const)('%s remains lesson-level without sentence navigation', (stage, label) => {
    window.history.replaceState({}, '', `/?stage=${stage}`)

    render(
      <TrainingSession
        lesson={allStageLesson}
        audioUrl={null}
        onExit={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading', { name: label, level: 1 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Previous sentence' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Sentence 1 of 2/)).not.toBeInTheDocument()
  })

  it('switches stages immediately and updates the URL while preserving other parameters', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', '/?lesson=demo')
    render(
      <TrainingSession
        lesson={allStageLesson}
        audioUrl={null}
        onExit={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Shadowing' }))

    expect(screen.getByRole('heading', { name: 'Shadowing', level: 1 })).toBeInTheDocument()
    expect(new URLSearchParams(window.location.search).get('stage')).toBe('shadowing')
    expect(new URLSearchParams(window.location.search).get('lesson')).toBe('demo')
  })

  it('ignores a stage override when development mode is disabled', () => {
    window.history.replaceState({}, '', '/?stage=shadowing')

    render(
      <TrainingSession
        lesson={lesson}
        audioUrl={null}
        onExit={vi.fn()}
        devMode={false}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Retell', level: 1 })).toBeInTheDocument()
  })

  it('hides the development switcher when development mode is disabled', () => {
    render(
      <TrainingSession
        lesson={lesson}
        audioUrl={null}
        onExit={vi.fn()}
        devMode={false}
      />,
    )

    expect(screen.queryByLabelText('Development stage controls')).not.toBeInTheDocument()
  })
})
