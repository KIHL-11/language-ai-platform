import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { cloudAuth } from '../auth/client'
import { CloudSessionProvider } from '../auth/CloudSessionProvider'
import { libraryItem, librarySummary } from '../testFixtures/libraryItem'
import type { LibraryItem, LibraryProgress } from '../types/library'

vi.mock('../api/apiBase', () => ({ API_BASE_URL: '' }))

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

const order = [...libraryItem.progress.training_order]
const fresh: LibraryProgress = {
  is_training: false, stage_index: 0, sentence_index: 0,
  completed_stages: [], training_order: [...order], lesson_memory_imported: false,
}
const ITEM_URL = `/api/library/items/${libraryItem.id}`

// A stateful stand-in for the cloud API: progress PATCHes are accepted only
// with the current revision, exactly like the PostgreSQL repository.
function cloudServer(owner = 'owner-a') {
  vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
  const state = {
    owner,
    item: {
      ...libraryItem,
      lesson: { ...libraryItem.lesson, source_url: 'https://example.test/synthetic-lesson' },
      progress: { ...fresh },
      revision: 1,
    } as LibraryItem & { revision: number },
    writes: [] as Array<{ url: string; body: Record<string, unknown>; csrf: string | null; owner: string }>,
  }
  const reply = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), {
    status, headers: { 'X-Authenticated-User': state.owner },
  }))
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    if (url === '/api/deployment') return reply({ mode: 'cloud' })
    if (url === '/api/account/me') return reply({ user_id: state.owner, csrf_token: 'a'.repeat(64) })
    if (url === '/api/account/logout') return Promise.resolve(new Response(null, { status: 204 }))
    if (url === '/api/library/items') return reply({ items: [{ ...librarySummary, title: `${state.owner} lesson` }] })
    if (url === ITEM_URL && (init.method ?? 'GET') === 'GET') return reply(state.item)
    if (url.startsWith(`${ITEM_URL}/`) && init.method === 'PATCH') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      state.writes.push({ url, body, csrf: new Headers(init.headers).get('X-CSRF-Token'), owner: state.owner })
      if (body.expected_revision !== state.item.revision) {
        return reply({ detail: { code: 'library_revision_conflict' } }, 409)
      }
      const { expected_revision: _ignored, ...progress } = body
      state.item = url.endsWith('/listening-progress')
        ? { ...state.item, listening_progress: progress as unknown as LibraryItem['listening_progress'], revision: state.item.revision + 1 }
        : { ...state.item, progress: progress as unknown as LibraryProgress, revision: state.item.revision + 1 }
      return reply(state.item)
    }
    return reply({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { state, fetchMock }
}

async function openLesson(user: ReturnType<typeof userEvent.setup>, owner = 'owner-a') {
  cloudAuth.invalidate()
  render(<CloudSessionProvider><p>Personal Desktop</p></CloudSessionProvider>)
  await user.click(await screen.findByRole('button', { name: 'My Library' }))
  await user.click(await screen.findByRole('button', { name: `${owner} lesson` }))
  await screen.findByRole('heading', { name: libraryItem.lesson.title })
}

describe('Cloud Library learning', () => {
  it('runs Guided training with revision-checked, CSRF-protected writes and no Personal or voice routes', async () => {
    const { state, fetchMock } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    expect(screen.getByText(/Guided training not started/)).toBeInTheDocument()
    const before = fetchMock.mock.calls.length

    await user.click(screen.getByRole('button', { name: 'Start guided training' }))
    await user.click(await screen.findByRole('button', { name: /start training/i }))
    await user.click(await screen.findByRole('button', { name: 'Complete Active Recall' }))

    await waitFor(() => expect(state.item.progress.stage_index).toBe(1))
    expect(state.writes.map((write) => write.body.expected_revision)).toEqual([1, 2])
    expect(state.writes.every((write) => write.csrf === 'a'.repeat(64))).toBe(true)
    expect(state.item.progress.completed_stages).toEqual(['active_recall'])
    const sinceOpen = fetchMock.mock.calls.slice(before).map(([url]) => String(url))
    expect(sinceOpen.filter((url) => /\/api\/(chunks|realtime)|user_id=|study/.test(url))).toEqual([])
    expect(new Set(sinceOpen)).toEqual(new Set([`${ITEM_URL}`, `${ITEM_URL}/progress`]))
    expect(screen.queryByText('Personal Desktop')).not.toBeInTheDocument()
  })

  it('continues from another tab’s further progress instead of overwriting it', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    await user.click(screen.getByRole('button', { name: 'Start guided training' }))
    // Another tab finishes three stages while this one is still on the overview.
    state.item = {
      ...state.item,
      progress: { ...fresh, is_training: true, stage_index: 3, completed_stages: order.slice(0, 3) },
      revision: 5,
    }

    await user.click(await screen.findByRole('button', { name: /start training/i }))

    expect(await screen.findByText(/moved ahead in another tab or device/)).toBeInTheDocument()
    await waitFor(() => expect(state.item.revision).toBeGreaterThanOrEqual(5))
    await new Promise((resolve) => setTimeout(resolve, 50))
    // Every accepted write after the conflict kept the further stage.
    expect(state.writes.filter((write) => Number(write.body.expected_revision) >= 5)
      .every((write) => write.body.stage_index === 3)).toBe(true)
    expect(state.item.progress.stage_index).toBe(3)
    expect(state.item.progress.completed_stages).toEqual(order.slice(0, 3))
    expect(state.writes[0].body.expected_revision).toBe(1)
    expect(screen.getByText('Mini Dictation', { selector: 'p, span, h2, h3, li' })).toBeInTheDocument()
  })

  it('opens Listening without Personal study routes and saves position with the revision', async () => {
    const { state, fetchMock } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    const before = fetchMock.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Open listening' }))
    await user.click(await screen.findByRole('button', { name: 'Blind' }))

    await waitFor(() => expect(state.item.listening_progress.mode).toBe('blind'))
    const sinceOpen = fetchMock.mock.calls.slice(before).map(([url]) => String(url))
    expect(state.writes[0]).toMatchObject({ url: `${ITEM_URL}/listening-progress`, body: { expected_revision: 1, mode: 'blind' } })
    expect(screen.queryByRole('heading', { name: 'Words & Chunks' })).not.toBeInTheDocument()
    expect(sinceOpen.filter((url) => /study|dictionary|chunks/.test(url))).toEqual([])
  })

  it('never writes the previous account’s progress after an account switch', async () => {
    const { state, fetchMock } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    await user.click(screen.getByRole('button', { name: 'Open listening' }))
    await screen.findByRole('button', { name: 'Blind' })
    const before = state.writes.length

    state.owner = 'owner-b'
    await act(() => cloudAuth.refresh())

    expect(await screen.findByRole('heading', { name: 'Words & Chunks' })).toBeInTheDocument()
    expect(screen.queryByText(libraryItem.lesson.title)).not.toBeInTheDocument()
    // Leaving Listening flushes its position; under owner-b it must be refused
    // before any request is made.
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/account/me').length).toBeGreaterThan(1))
    expect(state.writes.slice(before).filter((write) => write.owner === 'owner-b')).toEqual([])
  })

  it('stops writing after sign-out', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    await user.click(screen.getByRole('button', { name: 'Open listening' }))
    await screen.findByRole('button', { name: 'Blind' })

    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    // The sign-out broadcast also invalidates this tab: either gate message.
    expect(await screen.findByRole('heading', { name: 'Language AI — Cloud session' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Blind' })).not.toBeInTheDocument()
    expect(state.writes).toEqual([])
  })

  it('keeps an open Guided session when the same account is re-checked on focus', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    await user.click(screen.getByRole('button', { name: 'Start guided training' }))
    await user.click(await screen.findByRole('button', { name: /start training/i }))
    await waitFor(() => expect(state.item.revision).toBe(2))
    const generation = cloudAuth.getSnapshot().generation

    await act(async () => { fireEvent.focus(window); await Promise.resolve() })
    await act(() => cloudAuth.refresh())

    expect(cloudAuth.getSnapshot()).toMatchObject({ status: 'authenticated', userId: 'owner-a', generation })
    expect(screen.getByRole('heading', { name: 'Guided training' })).toBeInTheDocument()
    expect(screen.queryByText('Checking session…')).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Complete Active Recall' }))
    await waitFor(() => expect(state.item.revision).toBe(3))
  })

  it('reports an unavailable lesson without showing stale content', async () => {
    const { fetchMock } = cloudServer()
    const user = userEvent.setup()
    cloudAuth.invalidate()
    render(<CloudSessionProvider><p>Personal Desktop</p></CloudSessionProvider>)
    await user.click(await screen.findByRole('button', { name: 'My Library' }))
    fetchMock.mockImplementation((url: string) => Promise.resolve(new Response('{}', {
      status: url === ITEM_URL ? 404 : 200, headers: { 'X-Authenticated-User': 'owner-a' },
    })))

    await user.click(await screen.findByRole('button', { name: 'owner-a lesson' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('could not be opened')
    expect(screen.queryByRole('heading', { name: libraryItem.lesson.title })).not.toBeInTheDocument()
  })

  it('offers no realtime voice in cloud Live Dialogue even when the build enables it', async () => {
    vi.stubEnv('VITE_LIVE_DIALOGUE_PROVIDER', 'realtime')
    try {
      const { state, fetchMock } = cloudServer()
      state.item = {
        ...state.item,
        progress: { ...fresh, is_training: true, stage_index: 6, completed_stages: order.slice(0, 6) },
      }
      const user = userEvent.setup()
      await openLesson(user)
      await user.click(screen.getByRole('button', { name: 'Continue guided training' }))

      expect(await screen.findByRole('button', { name: 'Start Conversation' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Realtime Voice Practice' })).not.toBeInTheDocument()
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/realtime'))).toBe(false)
    } finally {
      vi.unstubAllEnvs()
    }
  })
})
