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
    if (url.startsWith('/api/review/queue')) {
      state.queues.push(new URL(url, 'https://learn.example'))
      return reply({ items: state.card.review.review_count === 0 ? [state.card] : [] })
    }
    if (url.startsWith('/api/review/items/') && init.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      state.ratings.push({ owner: state.owner, body })
      const answeredBy = state.owner
      const respond = () => {
        if (body.expected_review_count !== state.card.review.review_count) return reply({ detail: 'stale' }, 409, answeredBy)
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
  it('sends the learner time zone with the queue', async () => {
    const { state } = cloudServer()
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Europe/Berlin' } as Intl.ResolvedDateTimeFormatOptions)
    const user = userEvent.setup()
    await openCard(user)

    expect(state.queues.at(-1)?.searchParams.get('time_zone')).toBe('Europe/Berlin')
    expect(state.queues.at(-1)?.searchParams.get('user_id')).toBe('owner-a')
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
