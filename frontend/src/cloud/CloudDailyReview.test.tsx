import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { cloudAuth } from '../auth/client'
import { CloudSessionProvider } from '../auth/CloudSessionProvider'
import { studyItem } from '../testFixtures/studyBank'

vi.mock('../api/apiBase', () => ({ API_BASE_URL: '' }))

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

// A stand-in for the cloud review API: ratings succeed only for the current
// review_count, exactly like the PostgreSQL FOR UPDATE check.
function cloudServer() {
  vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
  const state = {
    owner: 'owner-a',
    card: { ...studyItem, id: 'card-1', text: 'synthetic phrase', active: true },
    queues: [] as URL[],
    ratings: [] as Array<{ owner: string; body: Record<string, unknown> }>,
    holdRating: false,
    quotaLeft: 10,
    quotaCode: 'daily_new_card_quota_exhausted' as string | null,
    extra: [] as typeof studyItem[],
    settings: { time_zone: 'UTC', configured: false, daily_new_limit: 10, new_cards_today: 0, new_cards_remaining: 10, quota_day: '2026-11-04', pending_time_zone: null, pending_effective_at: null } as Record<string, unknown>,
    settingsWrites: [] as Array<Record<string, unknown>>,
    release: () => {},
  }
  const reply = (value: unknown, status = 200, owner = state.owner) => Promise.resolve(new Response(JSON.stringify(value), {
    status, headers: { 'X-Authenticated-User': owner },
  }))
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    if (url === '/api/deployment') return reply({ mode: 'cloud' })
    if (url === '/api/account/me') return reply({ user_id: state.owner, csrf_token: 'a'.repeat(64) })
    if (url === '/api/account/logout') return Promise.resolve(new Response(null, { status: 204 }))
    if (url.startsWith('/api/review/summary')) {
      return reply({ due_words: 0, due_chunks: 0, new_words: 1, new_chunks: 0, active_words: 1, active_chunks: 0 })
    }
    if (url.startsWith('/api/review/settings')) {
      if (init.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        state.settingsWrites.push(body)
        state.settings = { ...state.settings, time_zone: body.time_zone, configured: true }
      }
      return reply({ ...state.settings, new_cards_remaining: state.quotaLeft, new_cards_today: 10 - state.quotaLeft })
    }
    if (url.startsWith('/api/review/queue')) {
      state.queues.push(new URL(url, 'https://learn.example'))
      return reply({ items: state.card.review.review_count === 0 ? [state.card, ...state.extra] : [] })
    }
    if (url.startsWith('/api/review/items/') && init.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      state.ratings.push({ owner: state.owner, body })
      const answeredBy = state.owner
      const respond = () => {
        if (body.expected_review_count !== state.card.review.review_count) return reply({ detail: 'stale' }, 409, answeredBy)
        if (state.card.review.review_count === 0) {
          if (state.quotaLeft <= 0) return reply({ detail: state.quotaCode ? { code: state.quotaCode, message: 'used up' } : 'Too many requests' }, 429, answeredBy)
          state.quotaLeft -= 1
        }
        state.card = { ...state.card, review: { ...state.card.review, review_count: state.card.review.review_count + 1 } }
        return reply({ item: state.card, previous_due_at: studyItem.review.due_at, next_due_at: '2026-11-05T09:00:00Z', interval_seconds: 86400 }, 200, answeredBy)
      }
      if (state.holdRating) {
        state.holdRating = false
        return new Promise<Response>((done) => { state.release = () => void respond().then(done) })
      }
      return respond()
    }
    return reply({ items: [] })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { state, fetchMock }
}

async function openCard(user: ReturnType<typeof userEvent.setup>) {
  cloudAuth.invalidate()
  render(<CloudSessionProvider><p>Personal Desktop</p></CloudSessionProvider>)
  await user.click(await screen.findByRole('button', { name: 'Daily Review' }))
  await user.click(await screen.findByRole('button', { name: 'Start Daily Review' }))
  await user.click(await screen.findByRole('button', { name: 'Reveal' }))
  await screen.findByRole('button', { name: 'Good' })
}

describe('Cloud Daily Review', () => {
  it('suggests the browser zone once and never sends a zone with the queue', async () => {
    const { state } = cloudServer()
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Europe/Berlin' } as Intl.ResolvedDateTimeFormatOptions)
    const user = userEvent.setup()
    await openCard(user)

    expect(state.settingsWrites).toEqual([{ user_id: 'owner-a', time_zone: 'Europe/Berlin' }])
    expect(state.queues.at(-1)?.searchParams.get('time_zone')).toBeNull()
    expect(state.queues.at(-1)?.searchParams.get('user_id')).toBe('owner-a')
  })

  it('leaves a configured zone alone', async () => {
    const { state } = cloudServer()
    state.settings = { ...state.settings, time_zone: 'Asia/Tokyo', configured: true }
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Europe/Berlin' } as Intl.ResolvedDateTimeFormatOptions)
    const user = userEvent.setup()
    await openCard(user)

    expect(state.settingsWrites).toEqual([])
    expect(screen.queryByText(/Europe\/Berlin/)).not.toBeInTheDocument()
  })

  it('shows the daily new-card limit instead of a stale-card reload', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openCard(user)
    state.quotaLeft = 0
    const queuesBefore = state.queues.length

    await user.click(screen.getByRole('button', { name: 'Good' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Today's new-card limit is used up")
    expect(alert).not.toHaveTextContent('changed elsewhere')
    expect(state.queues.length).toBe(queuesBefore)
    expect(state.card.review.review_count).toBe(0)
    expect(screen.queryByRole('button', { name: 'Good' })).not.toBeInTheDocument()
  })

  it('skips the remaining new cards but keeps due reviews once the limit is used up', async () => {
    const { state } = cloudServer()
    state.extra = [
      { ...studyItem, id: 'card-2', text: 'second new phrase', active: true },
      { ...studyItem, id: 'card-3', text: 'due phrase', active: true, review: { ...studyItem.review, review_count: 2 } },
    ]
    const user = userEvent.setup()
    await openCard(user)
    state.quotaLeft = 0

    await user.click(screen.getByRole('button', { name: 'Good' }))

    expect(await screen.findByRole('alert')).toHaveTextContent("Today's new-card limit is used up")
    await user.click(await screen.findByRole('button', { name: 'Reveal' }))
    expect(await screen.findByText('due phrase')).toBeInTheDocument()
    expect(screen.queryByText('second new phrase')).not.toBeInTheDocument()
  })

  it('a 429 without the quota code is an ordinary failed rating', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openCard(user)
    state.quotaLeft = 0
    state.quotaCode = null

    await user.click(screen.getByRole('button', { name: 'Good' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The rating was not saved')
    expect(alert).not.toHaveTextContent('new-card limit')
    expect(screen.getByText('synthetic phrase')).toBeInTheDocument()
  })

  it('reloads the queue when another tab rated the card first', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openCard(user)
    // The other tab rates the card while this one shows it.
    state.card = { ...state.card, review: { ...state.card.review, review_count: 1 } }
    const queuesBefore = state.queues.length

    await user.click(screen.getByRole('button', { name: 'Good' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere')
    await waitFor(() => expect(state.queues.length).toBe(queuesBefore + 1))
    expect(state.ratings.map((rating) => rating.body.expected_review_count)).toEqual([0])
    expect(state.card.review.review_count).toBe(1)
    expect(screen.queryByRole('button', { name: 'Good' })).not.toBeInTheDocument()
  })

  it('a rating in flight across an account switch is never shown to the new account', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openCard(user)
    state.holdRating = true
    await user.click(screen.getByRole('button', { name: 'Good' }))
    await waitFor(() => expect(state.ratings).toHaveLength(1))

    state.owner = 'owner-b'
    await act(() => cloudAuth.refresh())
    await act(async () => { state.release(); await new Promise((resolve) => setTimeout(resolve, 20)) })

    expect(screen.queryByText('synthetic phrase')).not.toBeInTheDocument()
    expect(screen.queryByText(/Next review/)).not.toBeInTheDocument()
    expect(state.ratings.map((rating) => rating.owner)).toEqual(['owner-a'])
  })

  it('stops after sign-out with a rating still in flight', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openCard(user)
    state.holdRating = true
    await user.click(screen.getByRole('button', { name: 'Good' }))
    await waitFor(() => expect(state.ratings).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    await act(async () => { state.release(); await new Promise((resolve) => setTimeout(resolve, 20)) })

    expect(await screen.findByRole('heading', { name: 'Language AI — Cloud session' })).toBeInTheDocument()
    expect(screen.queryByText('synthetic phrase')).not.toBeInTheDocument()
    expect(state.ratings).toHaveLength(1)
  })
})
