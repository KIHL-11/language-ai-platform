import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MediaController, MediaControllerStatus } from '../listening/mediaController'
import { libraryItem } from '../testFixtures/libraryItem'
import type { ShadowingMetrics } from '../types/library'
import ShadowingWorkspace from './ShadowingWorkspace'

const recordingBlob = new Blob(['recording'], { type: 'audio/webm' })
const reset = vi.fn()

vi.mock('../shadowing/useSyncRecorder', () => ({
  useSyncRecorder: () => ({
    status: 'recorded', countdown: null, blob: recordingBlob,
    playbackUrl: 'blob:recording', recordedDuration: 2.1, error: null,
    start: vi.fn(), reset,
  }),
}))

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
  recognized_text: 'Sentence 1', target_word_count: 2, recognized_word_count: 2,
  matched_count: 2, missed_count: 0, extra_count: 0, substituted_count: 0,
  transcript_match_percent: 100, reference_speech_duration_ms: 500,
  attempt_speech_duration_ms: 510, duration_delta_ms: 10, duration_delta_ratio: .02,
  start_delay_ms: 30, word_timing_mae_ms: null, pause_mae_ms: null,
  aligned_timed_word_count: 2, timing_reference_available: true,
  timing_unavailable_reason: null, sync_target_met: true,
  aligned_words: [
    { target: 'sentence', recognized: 'sentence', status: 'matched' },
    { target: '1', recognized: '1', status: 'matched' },
  ],
}

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

describe('Shadowing Workspace submission', () => {
  beforeEach(() => { reset.mockReset() })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('uploads one blob with optimistic count and renders the authoritative result once', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      sentence_id: 1,
      progress: {
        attempt_count: 1, target_hit_count: 1, consecutive_target_hits: 1,
        last_attempt_at: '2026-10-19T10:00:00Z', last_metrics: metrics,
        best_transcript_match_percent: 100, best_duration_delta_ratio: .02,
        best_abs_start_delay_ms: 30, best_word_timing_mae_ms: null, best_pause_mae_ms: null,
      },
      metrics, sync_target_met: true, stable_sync_achieved: false,
    }))
    vi.stubGlobal('fetch', fetchMock)
    render(<ShadowingWorkspace item={libraryItem} onBack={vi.fn()} controllerFactory={() => new FakeController()} />)

    expect(await screen.findByText('100.0%')).toBeInTheDocument()
    expect(screen.getByText('Sync target met')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect(form.get('sentence_id')).toBe('1')
    expect(form.get('expected_attempt_count')).toBe('0')
    expect(form.get('audio')).toBeInstanceOf(Blob)
    expect((form.get('audio') as Blob).size).toBe(recordingBlob.size)
  })

  it('handles a stale 409 without advancing or retrying the recording', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({}, 409))
    vi.stubGlobal('fetch', fetchMock)
    render(<ShadowingWorkspace item={libraryItem} onBack={vi.fn()} controllerFactory={() => new FakeController()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('updated elsewhere')
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('Sync target met')).not.toBeInTheDocument()
  })
})
