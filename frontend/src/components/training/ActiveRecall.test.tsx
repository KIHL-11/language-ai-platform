import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type {
  ChunkMemory,
  ChunkMemoryClient,
  PracticeOutcome,
} from '../../api/chunkMemory'
import type { Lesson } from '../../types/lesson'
import ActiveRecall from './ActiveRecall'

const lesson: Lesson = {
  id: 'current-lesson',
  title: 'Current lesson',
  source_url: 'https://example.com/current',
  source_language: 'en',
  target_language: 'zh',
  level: 'B1',
  audio_url: null,
  source: 'subtitles',
  sentences: [],
  training_plan: {
    active_recall: { enabled: true, count: 5 },
    blind_listening: { enabled: true },
    comprehension: { enabled: true },
    mini_dictation: { enabled: true },
    shadowing: { enabled: true },
    retell: { enabled: true },
    live_dialogue: { enabled: true },
  },
  training_order: ['active_recall', 'blind_listening'],
  ai: { provider: 'mock', status: 'complete' },
}

function chunk(id: string, text: string): ChunkMemory {
  return {
    id,
    user_id: 'learner',
    text,
    normalized_text: text.toLowerCase(),
    meaning: `Meaning of ${text}`,
    source_language: 'en',
    source_lesson_id: 'earlier-lesson',
    source_media_id: null,
    source_sentence_id: 1,
    source_stage: 'lesson',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    last_practiced_at: null,
    practice_count: 0,
    success_count: 0,
    failure_count: 0,
    mastery_score: 0,
    active: true,
  }
}

function clientWith(
  chunks: ChunkMemory[],
  recordChunkPractice = vi.fn().mockResolvedValue(chunks[0]),
): ChunkMemoryClient {
  return {
    importLessonChunks: vi.fn(),
    fetchActiveRecallChunks: vi.fn().mockResolvedValue(chunks),
    recordChunkPractice,
  }
}

describe('ActiveRecall', () => {
  it('requests a bounded current-lesson-excluding queue and preserves backend order', async () => {
    const user = userEvent.setup()
    const chunks = [chunk('older', 'first from server'), chunk('newer', 'second from server')]
    const client = clientWith(chunks)
    render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={vi.fn()} client={client} />,
    )

    expect(await screen.findByText('Meaning of first from server')).toBeInTheDocument()
    expect(client.fetchActiveRecallChunks).toHaveBeenCalledWith(
      {
        learnerId: 'learner',
        language: 'en',
        currentLessonId: 'current-lesson',
        limit: 5,
      },
      expect.any(AbortSignal),
    )

    await user.click(screen.getByRole('button', { name: 'Reveal chunk' }))
    await user.click(screen.getByRole('button', { name: 'Got it' }))
    expect(await screen.findByText('Meaning of second from server')).toBeInTheDocument()
  })

  it('makes an empty queue explicitly completable', async () => {
    const user = userEvent.setup()
    const onComplete = vi.fn()
    render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={onComplete} client={clientWith([])} />,
    )

    expect(await screen.findByText('No earlier chunks are ready for review yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Complete Active Recall' }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('turns a read failure into a completable unavailable state', async () => {
    const user = userEvent.setup()
    const onComplete = vi.fn()
    const client = clientWith([])
    vi.mocked(client.fetchActiveRecallChunks).mockRejectedValue(new Error('offline'))
    render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={onComplete} client={client} />,
    )

    expect(await screen.findByText(/Saved language is unavailable/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Complete Active Recall' }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it.each([
    ['Got it', 'success'],
    ['Almost', 'partial'],
    ['Missed', 'failure'],
  ] as const)('maps %s to one %s practice write', async (label, outcome) => {
    const user = userEvent.setup()
    const item = chunk('chunk-1', 'take it easy')
    const record = vi.fn().mockResolvedValue(item)
    const onComplete = vi.fn()
    render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={onComplete} client={clientWith([item], record)} />,
    )

    await screen.findByText('Meaning of take it easy')
    await user.click(screen.getByRole('button', { name: 'Reveal chunk' }))
    await user.click(screen.getByRole('button', { name: label }))

    await waitFor(() => expect(record).toHaveBeenCalledOnce())
    expect(record).toHaveBeenCalledWith('learner', 'chunk-1', outcome as PracticeOutcome)
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('prevents duplicate writes while a rating is pending', async () => {
    const user = userEvent.setup()
    const item = chunk('chunk-1', 'take it easy')
    let resolveWrite!: (value: ChunkMemory) => void
    const record = vi.fn().mockImplementation(
      () => new Promise<ChunkMemory>((resolve) => { resolveWrite = resolve }),
    )
    render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={vi.fn()} client={clientWith([item], record)} />,
    )

    await screen.findByText('Meaning of take it easy')
    await user.click(screen.getByRole('button', { name: 'Reveal chunk' }))
    const rating = screen.getByRole('button', { name: 'Got it' })
    await user.dblClick(rating)
    expect(record).toHaveBeenCalledOnce()
    resolveWrite(item)
  })

  it('allows progress after a practice write failure', async () => {
    const user = userEvent.setup()
    const item = chunk('chunk-1', 'take it easy')
    const onComplete = vi.fn()
    const record = vi.fn().mockRejectedValue(new Error('offline'))
    render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={onComplete} client={clientWith([item], record)} />,
    )

    await screen.findByText('Meaning of take it easy')
    await user.click(screen.getByRole('button', { name: 'Reveal chunk' }))
    await user.click(screen.getByRole('button', { name: 'Missed' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be saved')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('aborts a stale queue request on unmount', () => {
    const client = clientWith([])
    vi.mocked(client.fetchActiveRecallChunks).mockReturnValue(new Promise(() => {}))
    const view = render(
      <ActiveRecall lesson={lesson} learnerId="learner" onComplete={vi.fn()} client={client} />,
    )
    const signal = vi.mocked(client.fetchActiveRecallChunks).mock.calls[0][1]

    view.unmount()
    expect(signal?.aborted).toBe(true)
  })
})
