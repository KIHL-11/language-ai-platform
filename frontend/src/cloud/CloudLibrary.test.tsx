import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { cloudAuth } from '../auth/client'
import { CloudSessionProvider } from '../auth/CloudSessionProvider'
import { libraryItem, librarySummary } from '../testFixtures/libraryItem'
import { dictionaryEntry } from '../testFixtures/dictionary'
import { studyItem } from '../testFixtures/studyBank'
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
    // When set, the next PATCH stays in flight until release() is called.
    holdNext: false,
    release: () => {},
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
      if (state.holdNext) {
        state.holdNext = false
        const answeredBy = state.owner
        return new Promise<Response>((done) => {
          state.release = () => done(new Response(JSON.stringify({ ...state.item, revision: state.item.revision + 1 }), {
            headers: { 'X-Authenticated-User': answeredBy },
          }))
        })
      }
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
    localStorage.clear()
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
    // No Personal learner id is minted for cloud training (review L2).
    expect(localStorage.getItem('language_ai_learner_id_v1')).toBeNull()
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
    state.holdNext = true
    await user.click(await screen.findByRole('button', { name: 'Blind' }))
    await waitFor(() => expect(state.writes).toHaveLength(1))
    // A second change is queued behind the in-flight write.
    await user.click(screen.getByRole('button', { name: 'Show translation' }))

    state.owner = 'owner-b'
    await act(() => cloudAuth.refresh())
    await act(async () => { state.release(); await new Promise((resolve) => setTimeout(resolve, 20)) })

    expect(await screen.findByRole('heading', { name: 'Words & Chunks' })).toBeInTheDocument()
    expect(screen.queryByText(libraryItem.lesson.title)).not.toBeInTheDocument()
    // Only the write sent under owner-a ever reached the network; the queued
    // and unmount-flush writes were refused before any request.
    expect(state.writes.map((write) => write.owner)).toEqual(['owner-a'])
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')).toHaveLength(1)
  })

  it('stops writing after sign-out, including writes queued before it', async () => {
    const { state } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    await user.click(screen.getByRole('button', { name: 'Open listening' }))
    state.holdNext = true
    await user.click(await screen.findByRole('button', { name: 'Blind' }))
    await waitFor(() => expect(state.writes).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Show translation' }))

    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    await act(async () => { state.release(); await new Promise((resolve) => setTimeout(resolve, 20)) })

    // The sign-out broadcast also invalidates this tab: either gate message.
    expect(await screen.findByRole('heading', { name: 'Language AI — Cloud session' })).toBeInTheDocument()
    expect(state.writes).toHaveLength(1)
  })

  it('shows the error when going back finds the lesson gone (review L4)', async () => {
    const { fetchMock } = cloudServer()
    const user = userEvent.setup()
    await openLesson(user)
    await user.click(screen.getByRole('button', { name: 'Open listening' }))
    await screen.findByRole('button', { name: 'Blind' })
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url === ITEM_URL && (init?.method ?? 'GET') === 'GET'
      ? Promise.resolve(new Response('{}', { status: 404, headers: { 'X-Authenticated-User': 'owner-a' } }))
      : original(url, init))

    await user.click(screen.getByRole('button', { name: /back/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('could not be opened')
    expect(screen.queryByRole('button', { name: 'Blind' })).not.toBeInTheDocument()
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

  describe('Listening resume point across tabs', () => {
    async function openListeningIdle() {
      const server = cloudServer()
      const user = userEvent.setup()
      await openLesson(user)
      await user.click(screen.getByRole('button', { name: 'Open listening' }))
      await screen.findByRole('button', { name: 'Blind' })
      // Another tab plays on: the saved resume point moves to 2.75 s.
      server.state.item = {
        ...server.state.item,
        listening_progress: { ...server.state.item.listening_progress, last_position_seconds: 2.75, current_sentence_id: 3 },
        revision: server.state.item.revision + 1,
      }
      return { ...server, user }
    }

    it('an idle tab changing a display setting keeps the other tab’s position', async () => {
      const { state, user } = await openListeningIdle()

      await user.click(screen.getByRole('button', { name: 'Show translation' }))

      await waitFor(() => expect(state.item.listening_progress.translation_visible).toBe(false))
      expect(state.item.listening_progress).toMatchObject({ last_position_seconds: 2.75, current_sentence_id: 3 })
      expect(state.writes.filter((write) => write.body.last_position_seconds !== 2.75 && write.body.expected_revision === state.item.revision - 1)).toEqual([])
    })

    it('leaving an idle tab keeps the other tab’s position', async () => {
      const { state, user } = await openListeningIdle()

      await user.click(screen.getByRole('button', { name: /back/i }))
      await screen.findByRole('button', { name: 'Open listening' })

      expect(state.item.listening_progress).toMatchObject({ last_position_seconds: 2.75, current_sentence_id: 3 })
    })

    it('a deliberate rewind in this tab becomes the resume point', async () => {
      const { state, user } = await openListeningIdle()
      const [first] = libraryItem.lesson.sentences

      await user.click(screen.getByRole('button', { name: `Seek to ${first.text}` }))

      await waitFor(() => expect(state.item.listening_progress.last_position_seconds).toBe(first.start))
      expect(state.item.listening_progress.current_sentence_id).toBe(first.id)
    })
  })

  describe('B-3: dictionary and AI chunks from the cloud lesson', () => {
    function route(fetchMock: ReturnType<typeof vi.fn>, extra: (url: string, init: RequestInit) => Promise<Response> | null) {
      const original = fetchMock.getMockImplementation() as (url: string, init?: RequestInit) => Promise<Response>
      fetchMock.mockImplementation((url: string, init: RequestInit = {}) => extra(url, init) ?? original(url, init))
    }
    const owned = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), {
      status, headers: { 'X-Authenticated-User': 'owner-a' },
    }))

    function wordRoutes(fetchMock: ReturnType<typeof vi.fn>, initial: 'unsaved' | 'archived') {
      const calls: Array<{ url: string; method: string; body: Record<string, unknown> | null; csrf: string | null }> = []
      const card = { ...studyItem, id: 'study-word-1', text: 'Sentence', lemma: 'sentence', normalized_text: 'sentence', revision: 4,
        active: initial !== 'archived' }
      route(fetchMock, (url, init) => {
        const method = (init.method ?? 'GET').toUpperCase()
        const body = init.body ? JSON.parse(String(init.body)) : null
        if (url === '/api/dictionary/lookup') return owned({ ...dictionaryEntry, query: 'Sentence', surface_form: 'Sentence' })
        if (!/study-bank/.test(url)) return null
        calls.push({ url, method, body, csrf: new Headers(init.headers).get('X-CSRF-Token') })
        if (url.includes('/study-bank/words/status')) {
          return owned(initial === 'archived' ? { status: 'archived', item: card } : { status: 'unsaved', item: null })
        }
        if (url.endsWith('/study-bank/words') && method === 'POST') return owned({ item: { ...card, active: true }, created: true })
        if (url.startsWith('/api/study-bank/items/study-word-1') && method === 'PATCH') return owned({ ...card, active: true, revision: 5 })
        return null
      })
      return calls
    }

    async function openWord(user: ReturnType<typeof userEvent.setup>) {
      await openLesson(user)
      await user.click(screen.getByRole('button', { name: 'Open listening' }))
      await user.click((await screen.findAllByRole('button', { name: 'Look up Sentence' }))[0])
      return screen.findByRole('dialog')
    }

    it('saves a subtitle word to the cloud Study Bank as the session owner (B-4)', async () => {
      const { fetchMock } = cloudServer()
      const calls = wordRoutes(fetchMock, 'unsaved')
      localStorage.clear()
      const user = userEvent.setup()
      await openWord(user)

      await user.click(await screen.findByRole('button', { name: 'Save Word' }))

      expect(await screen.findByText('Saved')).toBeInTheDocument()
      const status = calls.find((call) => call.url.includes('/study-bank/words/status'))!
      const save = calls.find((call) => call.method === 'POST' && call.url.endsWith('/study-bank/words'))!
      expect(status.url).toMatch(new RegExp(`^${ITEM_URL}/study-bank/words/status\\?`))
      expect(new URL(status.url, 'https://learn.example').searchParams.get('user_id')).toBe('owner-a')
      expect(save).toMatchObject({ url: `${ITEM_URL}/study-bank/words`, method: 'POST', csrf: 'a'.repeat(64) })
      // The client names only the sentence and surface word; never lemma,
      // meaning, provenance or a local learner id.
      expect(Object.keys(save.body ?? {}).sort()).toEqual(['sentence_id', 'user_id', 'word'])
      expect(save.body).toMatchObject({ user_id: 'owner-a', word: 'Sentence' })
      expect(localStorage.getItem('language_ai_learner_id_v1')).toBeNull()
    })

    it('an archived word offers an explicit Restore with its revision, never an implicit save', async () => {
      const { fetchMock } = cloudServer()
      const calls = wordRoutes(fetchMock, 'archived')
      const user = userEvent.setup()
      await openWord(user)

      await user.click(await screen.findByRole('button', { name: /restore/i }))

      expect(await screen.findByText('Saved')).toBeInTheDocument()
      expect(calls.filter((call) => call.method === 'POST')).toEqual([])
      const restore = calls.find((call) => call.method === 'PATCH')!
      expect(restore.body).toEqual({ active: true, expected_revision: 4 })
      expect(new URL(restore.url, 'https://learn.example').searchParams.get('user_id')).toBe('owner-a')
    })

    it('drops a word\'s saved state when the account changes', async () => {
      const { fetchMock, state } = cloudServer()
      const calls = wordRoutes(fetchMock, 'unsaved')
      const user = userEvent.setup()
      await openWord(user)
      await screen.findByRole('button', { name: 'Save Word' })
      const before = calls.length

      state.owner = 'owner-b'
      await act(() => cloudAuth.refresh())

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Save Word' })).not.toBeInTheDocument()
      expect(calls.slice(before).filter((call) => call.url.includes('user_id=owner-a') || call.body?.user_id === 'owner-a')).toEqual([])
    })

    it('offers no Save Word when the form matches several lemmas (review M1)', async () => {
      const { fetchMock } = cloudServer()
      const calls = wordRoutes(fetchMock, 'unsaved')
      route(fetchMock, (url) => url === '/api/dictionary/lookup'
        ? owned({ ...dictionaryEntry, query: 'Sentence', surface_form: 'Sentence', status: 'ambiguous', lemma_candidates: ['leaf', 'leave'] })
        : null)
      const user = userEvent.setup()
      await openWord(user)

      expect(await screen.findByText(/matches several dictionary words/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Save Word' })).not.toBeInTheDocument()
      expect(calls.filter((call) => call.url.includes('/study-bank/words'))).toEqual([])
    })

    it('reloads a word changed elsewhere before restoring it (review L2)', async () => {
      const { fetchMock } = cloudServer()
      let statusCalls = 0
      const patches: Array<Record<string, unknown>> = []
      const card = { ...studyItem, id: 'study-word-1', text: 'Sentence', active: false, revision: 4 }
      route(fetchMock, (url, init) => {
        if (url === '/api/dictionary/lookup') return owned({ ...dictionaryEntry, query: 'Sentence', surface_form: 'Sentence' })
        if (url.includes('/study-bank/words/status')) {
          statusCalls += 1
          return owned({ status: 'archived', item: { ...card, revision: statusCalls === 1 ? 4 : 6 } })
        }
        if (url.startsWith('/api/study-bank/items/study-word-1') && init.method === 'PATCH') {
          const body = JSON.parse(String(init.body))
          patches.push(body)
          return body.expected_revision === 6 ? owned({ ...card, active: true, revision: 7 }) : owned({ detail: 'stale' }, 409)
        }
        return null
      })
      const user = userEvent.setup()
      await openWord(user)

      await user.click(await screen.findByRole('button', { name: /restore/i }))
      await waitFor(() => expect(statusCalls).toBe(2))
      await user.click(await screen.findByRole('button', { name: /restore/i }))

      expect(await screen.findByText('Saved')).toBeInTheDocument()
      expect(patches.map((body) => body.expected_revision)).toEqual([4, 6])
    })

    it('never shows a word status that arrives after the account changed (review L4)', async () => {
      const { fetchMock, state } = cloudServer()
      let release!: () => void
      route(fetchMock, (url) => {
        if (url === '/api/dictionary/lookup') return owned({ ...dictionaryEntry, query: 'Sentence', surface_form: 'Sentence' })
        if (url.includes('/study-bank/words/status')) {
          return new Promise<Response>((done) => {
            release = () => done(new Response(JSON.stringify({ status: 'archived', item: { ...studyItem, active: false } }), {
              headers: { 'X-Authenticated-User': 'owner-a' },
            }))
          })
        }
        return null
      })
      const user = userEvent.setup()
      await openWord(user)
      await waitFor(() => expect(release).toBeTypeOf('function'))

      state.owner = 'owner-b'
      await act(() => cloudAuth.refresh())
      await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 20)) })

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /restore/i })).not.toBeInTheDocument()
      expect(screen.queryByText('Previously saved · Archived')).not.toBeInTheDocument()
    })

    it('finds chunks in the lesson with provenance, saves edits only, then accepts', async () => {
      const { fetchMock } = cloudServer()
      const candidate = {
        candidate_id: 'candidate-1', state: 'proposed', version: 1, source_language: 'en',
        text: 'sentence', normalized_identity: 'sentence', suggested_meaning_zh: '句子', usage_note: '', example: '',
        explanation_of_value: '', quality_flags: [], proposal_model: 'mock', proposal_version: '1',
        existing_study_item_id: null, existing_study_item_active: null, accepted_study_item_id: null,
        evidence: { source_type: 'library_sentence', quote: 'Sentence 1.', source_label: 'Daily lesson', evidence_start: 0,
          evidence_end: 8, library_item_id: libraryItem.id, lesson_id: libraryItem.lesson.id, sentence_id: 1, start: 0, end: 1 },
      }
      const requests: Array<{ url: string; body: Record<string, unknown> }> = []
      route(fetchMock, (url, init) => {
        if (!url.startsWith('/api/ai/chunk-suggestions')) return null
        const body = init.body ? JSON.parse(String(init.body)) : {}
        requests.push({ url, body })
        if (url === '/api/ai/chunk-suggestions') {
          return owned({ batch_id: 'batch-1', user_id: 'owner-a', source_language: 'en', source_type: 'library_passage',
            source_label: 'Daily lesson', created_at: '2026-11-04T00:00:00Z', expires_at: null, status: 'ready', candidates: [candidate] })
        }
        const next = body.action === 'edit'
          ? { ...candidate, version: 2, suggested_meaning_zh: body.edit.suggested_meaning_zh }
          : { ...candidate, version: 3, state: 'accepted', accepted_study_item_id: 'study-1' }
        return owned({ candidate: next, study_item: null, created: false, warnings: [] })
      })
      const user = userEvent.setup()
      await openLesson(user)
      await user.click(screen.getByRole('button', { name: 'Find chunks' }))

      await user.click(await screen.findByRole('button', { name: 'Find chunks in this lesson' }))

      expect(requests[0].body).toMatchObject({
        user_id: 'owner-a', source_language: libraryItem.lesson.source_language,
        source: { source_type: 'library_passage', library_item_id: libraryItem.id },
      })
      expect(await screen.findByLabelText('Candidate source')).toHaveTextContent('Sentence 1 · 0:00 · Daily lesson')
      const meaning = screen.getByLabelText('Chinese explanation')
      expect(screen.getByRole('button', { name: 'Save edits' })).toBeDisabled()
      await user.clear(meaning)
      await user.type(meaning, '句子（已编辑）')
      await user.click(screen.getByRole('button', { name: 'Save edits' }))

      expect(await screen.findByText(/Edits saved/)).toBeInTheDocument()
      expect(requests[1].body).toMatchObject({ action: 'edit', expected_version: 1, user_id: 'owner-a',
        edit: { suggested_meaning_zh: '句子（已编辑）' } })
      expect(screen.getByText('proposed')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'Accept' }))
      await waitFor(() => expect(requests[2]?.body).toMatchObject({ action: 'accept', expected_version: 2 }))
    })

    it('reloads a candidate changed in another tab instead of overwriting it', async () => {
      const { fetchMock } = cloudServer()
      const candidate = {
        candidate_id: 'candidate-1', state: 'proposed', version: 1, source_language: 'en',
        text: 'sentence', normalized_identity: 'sentence', suggested_meaning_zh: '句子', usage_note: '', example: '',
        explanation_of_value: '', quality_flags: [], proposal_model: 'mock', proposal_version: '1',
        existing_study_item_id: null, existing_study_item_active: null, accepted_study_item_id: null,
        evidence: { source_type: 'library_sentence', quote: 'Sentence 1.', source_label: 'Daily lesson', evidence_start: 0, evidence_end: 8 },
      }
      const batch = (value: typeof candidate) => ({ batch_id: 'batch-1', user_id: 'owner-a', source_language: 'en',
        source_type: 'library_passage', source_label: 'Daily lesson', created_at: '2026-11-04T00:00:00Z', expires_at: null,
        status: 'ready', candidates: [value] })
      route(fetchMock, (url, init) => {
        if (url === '/api/ai/chunk-suggestions') return owned(batch(candidate))
        if (url.endsWith('/decisions') && init.method === 'POST') return owned({ detail: 'Candidate decision is stale' }, 409)
        if (url.startsWith('/api/ai/chunk-suggestions/batch-1?')) return owned(batch({ ...candidate, state: 'rejected', version: 2 }))
        return null
      })
      const user = userEvent.setup()
      await openLesson(user)
      await user.click(screen.getByRole('button', { name: 'Find chunks' }))
      await user.click(await screen.findByRole('button', { name: 'Find chunks in this lesson' }))

      await user.click(await screen.findByRole('button', { name: 'Accept' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere')
      expect(screen.getByText('rejected')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    })
  })
})
