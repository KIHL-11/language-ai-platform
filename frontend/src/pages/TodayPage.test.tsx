import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LEARNER_ID_STORAGE_KEY } from '../session/learnerSession'
import TodayPage from './TodayPage'


const learnerId = '123e4567-e89b-42d3-a456-426614174000'
const overview = {
  review: { due_words: 2, due_chunks: 1, new_words: 3, new_chunks: 4, active_words: 5, active_chunks: 6 },
  weak_chunks: [{
    study_item_id: 'study-one', kind: 'chunk', text: 'as far as I know', source_language: 'en',
    review_status: 'due', due_at: '2026-10-20T00:00:00Z', review_count: 2, lapse_count: 1,
    last_rating: 'again', last_reviewed_at: '2026-10-20T00:00:00Z', recent_targeted_session_count: 1,
    recent_used_session_count: 0, recent_use_count: 0, last_used_at: null, speaking_evidence: null,
    source_sentence_count: 1, attempted_source_sentence_count: 0, stable_source_sentence_count: 0,
    shadowing_last_practiced_at: null,
    preferred_shadowing_source: { library_item_id: 'library-one', sentence_id: 3, media_title: 'Paris' },
    guided_practice_count: 2, guided_success_count: 1, guided_failure_count: 1, guided_last_practiced_at: null,
  }],
  continue_listening: [{ library_item_id: 'library-one', title: 'Paris', source_language: 'de', last_position_seconds: 523, current_sentence_id: 3, updated_at: '2026-10-20T00:00:00Z' }],
  speaking_suggestions: [{ speaking_session_id: 'session-one', suggestion_index: 0, source_language: 'en', text: 'that makes sense', meaning: '有道理', kind: 'chunk', originating_turn: 2, started_at: '2026-10-20T00:00:00Z', target_languages: ['zh-CN'] }],
  next_actions: [{ type: 'review', label: 'Review due Words & Chunks', source_language: null, study_item_id: null, library_item_id: null, sentence_id: null, speaking_session_id: null, suggestion_index: null }],
  has_study_items: true, has_library_items: true, has_speaking_sessions: true,
}
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

describe('Today', () => {
  const fetchMock = vi.fn()
  const callbacks = { onReview: vi.fn(), onSpeak: vi.fn(), onListen: vi.fn(), onShadow: vi.fn() }

  beforeEach(() => {
    localStorage.clear(); localStorage.setItem(LEARNER_ID_STORAGE_KEY, learnerId)
    fetchMock.mockReset(); fetchMock.mockImplementation((_url: string, options?: RequestInit) => {
      if (options?.method === 'POST') return Promise.resolve(response({ item: {}, created: true, chunk_memory_synchronized: true }))
      return Promise.resolve(response(overview))
    }); vi.stubGlobal('fetch', fetchMock)
    Object.values(callbacks).forEach((value) => value.mockReset())
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('renders bounded factual sections and routes practice actions', async () => {
    const user = userEvent.setup(); render(<TodayPage {...callbacks} />)
    expect(await screen.findByText(/Due now: Words 2/)).toHaveTextContent('Chunks 1')
    expect(screen.getByText('as far as I know')).toBeInTheDocument()
    expect(screen.getByText(/Not yet used in recent Speaking/)).toBeInTheDocument()
    expect(screen.getByText(/Continue at 8:43/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Review now' })); expect(callbacks.onReview).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Practice in Speaking' })); expect(callbacks.onSpeak).toHaveBeenCalledWith('en', ['study-one'])
    await user.click(screen.getByRole('button', { name: 'Practice in Shadowing' })); expect(callbacks.onShadow).toHaveBeenCalledWith('library-one', 3)
    await user.click(screen.getByRole('button', { name: /Paris · Continue/ })); expect(callbacks.onListen).toHaveBeenCalledWith('library-one')
  })

  it('applies a language filter without mutating through GET', async () => {
    const user = userEvent.setup(); render(<TodayPage {...callbacks} />)
    await screen.findByText('as far as I know')
    await user.click(screen.getByRole('button', { name: 'German' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('language=de'))).toBe(true))
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method).length).toBe(0)
  })

  it('preselects a unique target language and saves only after an explicit click', async () => {
    const user = userEvent.setup(); render(<TodayPage {...callbacks} />)
    const save = await screen.findByRole('button', { name: 'Save to Words & Chunks' })
    expect(screen.getByLabelText('Target language for that makes sense')).toHaveValue('zh-CN')
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(0)
    await user.click(save)
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1))
    expect(JSON.parse(String(fetchMock.mock.calls.find(([, options]) => options?.method === 'POST')?.[1]?.body))).toEqual({ user_id: learnerId, target_language: 'zh-CN' })
  })

  it('requires explicit target language when none exists and degrades on overview failure', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...overview, speaking_suggestions: [{ ...overview.speaking_suggestions[0], target_languages: [] }] }))
    const user = userEvent.setup(); const { unmount } = render(<TodayPage {...callbacks} />)
    const save = await screen.findByRole('button', { name: 'Save to Words & Chunks' })
    expect(save).toBeDisabled()
    await user.type(screen.getByPlaceholderText('Enter target language'), 'de-DE')
    expect(save).toBeEnabled()
    unmount()
    fetchMock.mockRejectedValueOnce(new Error('broken'))
    render(<TodayPage {...callbacks} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('learning data was not changed')
  })
})
