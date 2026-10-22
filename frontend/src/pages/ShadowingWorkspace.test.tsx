import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MediaController, MediaControllerStatus } from '../listening/mediaController'
import { libraryItem } from '../testFixtures/libraryItem'
import type { ShadowingMetrics } from '../types/library'
import ShadowingWorkspace from './ShadowingWorkspace'

class FakeController implements MediaController {
  play = vi.fn(async () => undefined)
  pause = vi.fn()
  seek = vi.fn()
  getCurrentTime = vi.fn(() => 0)
  getDuration = vi.fn(() => 3)
  setPlaybackRate = vi.fn((rate: number) => rate)
  getPlaybackRate = vi.fn(() => 1)
  getStatus = vi.fn((): MediaControllerStatus => 'ready')
  destroy = vi.fn()
}

const metrics: ShadowingMetrics = {
  recognized_text: 'Sentence two', target_word_count: 2, recognized_word_count: 2,
  matched_count: 1, missed_count: 0, extra_count: 0, substituted_count: 1,
  transcript_match_percent: 50, reference_speech_duration_ms: 700,
  attempt_speech_duration_ms: 800, duration_delta_ms: 100, duration_delta_ratio: .14,
  start_delay_ms: 180, word_timing_mae_ms: null, pause_mae_ms: null,
  aligned_timed_word_count: 1, timing_reference_available: true,
  timing_unavailable_reason: null, sync_target_met: false,
  aligned_words: [
    { target: 'Sentence', recognized: 'Sentence', status: 'matched' },
    { target: '2', recognized: 'two', status: 'substituted' },
  ],
}

function progressedItem() {
  return {
    ...libraryItem,
    shadowing_progress: {
      last_sentence_id: 2,
      last_practiced_at: '2026-10-19T09:00:00Z',
      sentences: {
        '2': {
          attempt_count: 3, target_hit_count: 3, consecutive_target_hits: 3,
          last_attempt_at: '2026-10-19T09:00:00Z', last_metrics: metrics,
          best_transcript_match_percent: 94, best_duration_delta_ratio: .1,
          best_abs_start_delay_ms: 180, best_word_timing_mae_ms: null, best_pause_mae_ms: null,
        },
      },
    },
  }
}

describe('Shadowing Workspace', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined })
  })
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

  it('restores the last sentence and measured result without restoring a recording', () => {
    const controller = new FakeController()
    render(<ShadowingWorkspace item={progressedItem()} onBack={vi.fn()} controllerFactory={() => controller} />)
    expect(screen.getByText(/Sentence 2 of 3/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Last attempt' })).toBeInTheDocument()
    expect(screen.getByText('50.0%')).toBeInTheDocument()
    expect(screen.getByText(/Start delay: \+180 ms/)).toBeInTheDocument()
    expect(screen.getByText('Stable sync achieved')).toBeInTheDocument()
    expect(screen.queryByLabelText('Play My Recording')).not.toBeInTheDocument()
  })

  it('previews reference speed, seeks exact sentence bounds, and navigates/reset results', async () => {
    vi.useFakeTimers()
    const controller = new FakeController()
    render(<ShadowingWorkspace item={progressedItem()} onBack={vi.fn()} controllerFactory={() => controller} />)
    fireEvent.click(screen.getByRole('button', { name: '0.75x' }))
    fireEvent.click(screen.getByRole('button', { name: 'Play sentence' }))
    expect(controller.seek).toHaveBeenCalledWith(1)
    expect(controller.setPlaybackRate).toHaveBeenCalledWith(.75)
    expect(controller.play).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Next sentence' }))
    expect(screen.getByText(/Sentence 3 of 3/)).toBeInTheDocument()
    expect(screen.queryByText('50.0%')).not.toBeInTheDocument()
    act(() => vi.runOnlyPendingTimers())
  })

  it('degrades honestly without saved audio and requests no microphone before explicit action', async () => {
    const user = userEvent.setup()
    const item = {
      ...libraryItem,
      persistent_audio: { status: 'unavailable' as const, filename: null, content_type: null },
    }
    render(<ShadowingWorkspace item={item} onBack={vi.fn()} controllerFactory={() => { throw new Error('must not create') }} />)
    expect(screen.getByText('Precise sync timing requires saved reference audio. Transcript Match remains available.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play sentence' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Start Sync Attempt' }))
    expect(screen.getByRole('alert')).toHaveTextContent('microphone is not available')
  })

  it('hides noise-only sentences, cleans mixed speech, and skips hidden deep links', () => {
    const item = {
      ...libraryItem,
      lesson: {
        ...libraryItem.lesson,
        sentences: libraryItem.lesson.sentences.map((sentence) => sentence.id === 2
          ? { ...sentence, text: '[music] [APPLAUSE]' }
          : sentence.id === 3 ? { ...sentence, text: 'Keep [SINGING] going.' } : sentence),
      },
      shadowing_progress: { ...libraryItem.shadowing_progress, last_sentence_id: 2 },
    }
    render(<ShadowingWorkspace item={item} initialSentenceId={2} onBack={vi.fn()} controllerFactory={() => new FakeController()} />)
    expect(screen.getByText(/Sentence 2 of 2/)).toBeInTheDocument()
    expect(screen.getAllByText('Keep going.').length).toBeGreaterThan(0)
    expect(screen.queryByText('[music] [APPLAUSE]')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Previous sentence' }))
    expect(screen.getByText(/Sentence 1 of 2/)).toBeInTheDocument()
  })
})
