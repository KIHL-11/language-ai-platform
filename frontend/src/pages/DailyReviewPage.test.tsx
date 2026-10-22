import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LEARNER_ID_STORAGE_KEY } from '../session/learnerSession'
import { studyItem } from '../testFixtures/studyBank'
import type { StudyItem } from '../types/studyBank'
import { formatNextReview } from '../review/formatNextReview'
import DailyReviewPage from './DailyReviewPage'


const LEARNER_ID = '123e4567-e89b-42d3-a456-426614174000'
const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const chunk: StudyItem = {
  ...studyItem,
  id: 'study-chunk', kind: 'chunk', text: 'figure it out', normalized_text: 'figure it out',
  lemma: null, part_of_speech: null, example: null, usage: 'solve something', note: 'Useful phrase',
}
const summary = { due_words: 4, due_chunks: 3, new_words: 6, new_chunks: 2, active_words: 10, active_chunks: 5 }

function normalFetch(url: string, options?: RequestInit) {
  if (String(url).includes('/summary')) return Promise.resolve(response(summary))
  if (String(url).includes('/queue')) return Promise.resolve(response({ items: [studyItem, chunk] }))
  if (options?.method === 'POST') {
    const body = JSON.parse(String(options.body))
    return Promise.resolve(response({
      item: { ...studyItem, review: { ...studyItem.review, review_count: 1, [`${body.rating}_count`]: 1, last_rating: body.rating, last_reviewed_at: '2026-10-19T12:00:00Z' } },
      previous_due_at: studyItem.review.due_at,
      next_due_at: '2026-10-20T12:00:00Z',
      interval_seconds: 86400,
    }))
  }
  return Promise.resolve(response({}))
}

async function openFirstCard(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('Due now')
  await user.click(screen.getByRole('button', { name: 'Start Daily Review' }))
  await screen.findByText(/Card 1 of/)
}

describe('Daily Review', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem(LEARNER_ID_STORAGE_KEY, LEARNER_ID)
    fetchMock.mockReset()
    fetchMock.mockImplementation(normalFetch)
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('loads due/new counts and applies language and kind filters to the queue', async () => {
    const user = userEvent.setup()
    render(<DailyReviewPage />)
    expect((await screen.findAllByText('Words', { selector: 'p' }))[0]).toHaveTextContent('Words 4')
    expect(screen.getAllByText('Chunks', { selector: 'p' })[0]).toHaveTextContent('Chunks 3')
    expect(screen.getAllByText('Words', { selector: 'p' })[1]).toHaveTextContent('Words 6')
    expect(screen.getAllByText('Chunks', { selector: 'p' })[1]).toHaveTextContent('Chunks 2')
    await user.click(screen.getByRole('button', { name: 'German' }))
    await user.click(screen.getByRole('button', { name: 'Chunks' }))
    await user.click(screen.getByRole('button', { name: 'Start Daily Review' }))
    const queueUrl = String(fetchMock.mock.calls.find(([url]) => String(url).includes('/queue'))?.[0])
    expect(queueUrl).toContain('language=de')
    expect(queueUrl).toContain('kind=chunk')
  })

  it('keeps the target and rating controls hidden until Reveal, then shows metadata and provenance', async () => {
    const user = userEvent.setup()
    render(<DailyReviewPage />)
    await openFirstCard(user)
    expect(screen.getByText('解决')).toBeInTheDocument()
    expect(screen.queryByText('figuring')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Good' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    expect(screen.getByText('figuring')).toBeInTheDocument()
    expect(screen.getByText(/Lemma: figure/)).toHaveTextContent('verb')
    expect(screen.getByText(/Example:/)).toHaveTextContent('We can figure it out.')
    expect(screen.getByText('Daily lesson')).toBeInTheDocument()
    expect(screen.getByText(/1.0s/)).toHaveTextContent('We can figure it out.')
  })

  it('uses translation and generic fallback prompts without exposing source text', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/summary')) return Promise.resolve(response(summary))
      return Promise.resolve(response({ items: [{ ...studyItem, meaning: '', example: null }] }))
    })
    const user = userEvent.setup()
    const { unmount } = render(<DailyReviewPage />)
    await openFirstCard(user)
    expect(screen.getByText('我们可以解决。')).toBeInTheDocument()
    expect(screen.queryByText('We can figure it out.')).not.toBeInTheDocument()
    unmount()

    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/summary')) return Promise.resolve(response(summary))
      return Promise.resolve(response({ items: [{ ...studyItem, meaning: '', example: null, provenance: [{ ...studyItem.provenance[0], sentence_translation: '' }] }] }))
    })
    render(<DailyReviewPage />)
    await openFirstCard(userEvent.setup())
    expect(screen.getByText('Recall this saved word.')).toBeInTheDocument()
  })

  it('handles Speaking provenance without exposing it before Reveal', async () => {
    const speakingItem = { ...studyItem, meaning: '', provenance: [{ source_type: 'speaking' as const, speaking_session_id: 'session-one', suggestion_index: 0, originating_turn: 1, provider: 'gemini-live' as const, mode: 'review_weak' as const, media_title_snapshot: null, evidence_text: 'That makes sense.' }] }
    fetchMock.mockImplementation((url: string) => String(url).includes('/summary') ? Promise.resolve(response(summary)) : Promise.resolve(response({ items: [speakingItem] })))
    const user = userEvent.setup(); render(<DailyReviewPage />); await openFirstCard(user)
    expect(screen.getByText('Recall this saved word.')).toBeInTheDocument()
    expect(screen.queryByText('That makes sense.')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    expect(screen.getByText('Speaking session')).toBeInTheDocument()
    expect(screen.getByText(/That makes sense/)).toBeInTheDocument()
  })

  it.each(['Again', 'Hard', 'Good', 'Easy'])('sends %s with expected count and advances exactly once', async (label) => {
    const user = userEvent.setup()
    render(<DailyReviewPage />)
    await openFirstCard(user)
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    await user.click(screen.getByRole('button', { name: label }))
    expect(await screen.findByText('Card 2 of 2')).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([, options]) => options?.method === 'POST')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      user_id: LEARNER_ID,
      rating: label.toLowerCase(),
      expected_review_count: 0,
    })
    expect(screen.getByText('Card 2 of 2')).toBeInTheDocument()
  })

  it('locks controls while submitting and prevents a double rating', async () => {
    let resolveRating: ((value: unknown) => void) | undefined
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/summary')) return Promise.resolve(response(summary))
      if (String(url).includes('/queue')) return Promise.resolve(response({ items: [studyItem, chunk] }))
      if (options?.method === 'POST') return new Promise((resolve) => { resolveRating = resolve })
      return Promise.resolve(response({}))
    })
    const user = userEvent.setup()
    render(<DailyReviewPage />)
    await openFirstCard(user)
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    const good = screen.getByRole('button', { name: 'Good' })
    await user.click(good)
    expect(good).toBeDisabled()
    await user.click(good)
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    resolveRating?.(response({ item: { ...studyItem, review: { ...studyItem.review, review_count: 1 } }, previous_due_at: studyItem.review.due_at, next_due_at: '2026-10-20T12:00:00Z', interval_seconds: 86400 }))
    expect(await screen.findByText('Card 2 of 2')).toBeInTheDocument()
  })

  it('refreshes safely on conflict and does not advance on another backend failure', async () => {
    let ratingStatus = 409
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/summary')) return Promise.resolve(response(summary))
      if (String(url).includes('/queue')) return Promise.resolve(response({ items: [studyItem, chunk] }))
      if (options?.method === 'POST') return Promise.resolve(response({}, ratingStatus))
      return Promise.resolve(response({}))
    })
    const user = userEvent.setup()
    render(<DailyReviewPage />)
    await openFirstCard(user)
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    await user.click(screen.getByRole('button', { name: 'Good' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('refreshed')
    expect(screen.getByText('Card 1 of 2')).toBeInTheDocument()
    ratingStatus = 500
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    await user.click(screen.getByRole('button', { name: 'Good' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('remains open')
    expect(screen.getByText('Card 1 of 2')).toBeInTheDocument()
  })

  it('shows completion counts, refreshes summary, and offers Review More', async () => {
    const user = userEvent.setup()
    render(<DailyReviewPage />)
    await openFirstCard(user)
    for (const rating of ['Good', 'Again']) {
      await user.click(screen.getByRole('button', { name: 'Reveal' }))
      await user.click(screen.getByRole('button', { name: rating }))
    }
    expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeInTheDocument()
    expect(screen.getByText('Reviewed: 2')).toBeInTheDocument()
    expect(screen.getByText(/Again: 1/)).toHaveTextContent('Good: 1')
    expect(screen.getByRole('button', { name: 'Review More' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/summary')).length).toBeGreaterThan(1)
  })

  it('offers Speaking only for Again/Hard chunks with a bounded language launch', async () => {
    const cards = [
      { ...chunk, id: 'again-chunk' },
      { ...chunk, id: 'hard-chunk', text: 'hard phrase' },
      { ...chunk, id: 'good-chunk', text: 'good phrase' },
      { ...studyItem, id: 'again-word' },
    ]
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/summary')) return Promise.resolve(response(summary))
      if (String(url).includes('/queue')) return Promise.resolve(response({ items: cards }))
      if (options?.method === 'POST') return normalFetch(url, options)
      return Promise.resolve(response({}))
    })
    const onSpeak = vi.fn(); const user = userEvent.setup(); render(<DailyReviewPage onSpeak={onSpeak} />)
    await openFirstCard(user)
    for (const rating of ['Again', 'Hard', 'Good', 'Again']) {
      await user.click(screen.getByRole('button', { name: 'Reveal' }))
      await user.click(screen.getByRole('button', { name: rating }))
    }
    const launch = await screen.findByRole('button', { name: 'Practice difficult chunks in Speaking' })
    await user.click(launch)
    expect(onSpeak).toHaveBeenCalledWith('en', ['again-chunk', 'hard-chunk'])
  })

  it('distinguishes an empty Study Bank from nothing ready', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...summary, active_words: 0, active_chunks: 0, due_words: 0, due_chunks: 0, new_words: 0, new_chunks: 0 }))
    const { unmount } = render(<DailyReviewPage />)
    expect(await screen.findByText('No curated study items yet.')).toBeInTheDocument()
    expect(screen.getByText(/Listening or My Library/)).toBeInTheDocument()
    unmount()
    fetchMock.mockResolvedValueOnce(response({ ...summary, due_words: 0, due_chunks: 0, new_words: 0, new_chunks: 0 }))
    render(<DailyReviewPage />)
    expect(await screen.findByText('Nothing is ready for review right now.')).toBeInTheDocument()
  })
})

describe('next review formatting', () => {
  const now = new Date('2026-10-19T12:00:00Z')
  it('uses backend timestamps for due, minute, tomorrow, day, and calendar labels', () => {
    expect(formatNextReview('2026-10-19T11:00:00Z', now)).toBe('Due now')
    expect(formatNextReview('2026-10-19T12:10:00Z', now)).toBe('In 10 min')
    expect(formatNextReview('2026-10-19T14:00:00Z', now)).toBe('In 2 hours')
    expect(formatNextReview('2026-10-20T12:00:00Z', now)).toBe('Tomorrow')
    expect(formatNextReview('2026-10-22T12:00:00Z', now)).toBe('In 3 days')
    expect(formatNextReview('2026-10-28T12:00:00Z', now)).toMatch(/Oct 28/)
  })
})
