import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { libraryItem, librarySummary } from '../testFixtures/libraryItem'
import LibraryPage from './LibraryPage'


const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

function routeFetch(url: string, options?: RequestInit) {
  const value = String(url)
  if (options?.method === 'DELETE') return Promise.resolve(response(null, 204))
  if (options?.method === 'PATCH') {
    const progress = JSON.parse(String(options.body))
    return Promise.resolve(response({ ...libraryItem, progress }))
  }
  if (value.endsWith('/api/library/items')) {
    return Promise.resolve(response({ items: [librarySummary] }))
  }
  return Promise.resolve(response(libraryItem))
}

describe('My Library', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(routeFetch)
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('shows a clear empty state', async () => {
    fetchMock.mockResolvedValue(response({ items: [] }))
    render(<LibraryPage onOpen={vi.fn()} />)

    expect(await screen.findByText(/Your Library is empty/)).toBeInTheDocument()
  })

  it('renders lightweight summaries and persistent audio status', async () => {
    render(<LibraryPage onOpen={vi.fn()} />)

    expect(await screen.findByText(librarySummary.title)).toBeInTheDocument()
    expect(screen.getByText(/English · B2 · 3 sentences/)).toBeInTheDocument()
    expect(screen.getByText(/In progress · Audio saved/)).toBeInTheDocument()
  })

  it('filters by language and searches title or source', async () => {
    const user = userEvent.setup()
    const german = {
      ...librarySummary,
      id: 'german',
      lesson_id: 'german-lesson',
      title: 'German travel',
      source_url: 'https://example.test/german-trip',
      source_language: 'de' as const,
    }
    fetchMock.mockResolvedValue(response({ items: [librarySummary, german] }))
    render(<LibraryPage onOpen={vi.fn()} />)
    await screen.findByText(german.title)

    await user.click(screen.getByRole('button', { name: 'German' }))
    expect(screen.getByText(german.title)).toBeInTheDocument()
    expect(screen.queryByText(librarySummary.title)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'All' }))
    await user.type(screen.getByLabelText('Search My Library'), 'youtube.com')
    expect(screen.getByText(librarySummary.title)).toBeInTheDocument()
    expect(screen.queryByText(german.title)).not.toBeInTheDocument()
  })

  it('loads detail and displays transcript timestamps and translations', async () => {
    const user = userEvent.setup()
    render(<LibraryPage onOpen={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'View Transcript' }))

    expect(screen.getByRole('heading', { name: 'Transcript' })).toBeInTheDocument()
    expect(screen.getByText('Sentence 3.')).toBeInTheDocument()
    expect(screen.getByText('句子 3。')).toBeInTheDocument()
    expect(screen.getByText('2.0–3.0s')).toBeInTheDocument()
  })

  it('continues from the saved stage and sentence without regeneration', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(<LibraryPage onOpen={onOpen} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Continue Training' }))

    expect(onOpen).toHaveBeenCalledWith(
      expect.objectContaining({
        progress: expect.objectContaining({ stage_index: 3, sentence_index: 2 }),
      }),
      true,
    )
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/lesson/from-url'))).toBe(false)
  })

  it('opens the saved Lesson for review with its persistent audio URL', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(<LibraryPage onOpen={onOpen} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Open Lesson' }))

    expect(onOpen).toHaveBeenCalledWith(
      expect.objectContaining({
        lesson: expect.objectContaining({
          audio_url: '/api/library/items/library-item/audio',
        }),
      }),
      false,
    )
  })

  it('resets Train Again to canonical Active Recall without regeneration', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(<LibraryPage onOpen={onOpen} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Train Again' }))

    await waitFor(() => expect(onOpen).toHaveBeenCalled())
    const patch = fetchMock.mock.calls.find(([, options]) => options?.method === 'PATCH')
    expect(JSON.parse(patch?.[1]?.body as string)).toMatchObject({
      stage_index: 0,
      sentence_index: 0,
      completed_stages: [],
      training_order: libraryItem.lesson.training_order,
    })
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/lesson/from-url'))).toBe(false)
  })

  it('handles missing persistent audio explicitly', async () => {
    const user = userEvent.setup()
    fetchMock.mockImplementation((url: string) => String(url).endsWith('/api/library/items')
      ? Promise.resolve(response({ items: [{ ...librarySummary, audio_available: false }] }))
      : Promise.resolve(response({
        ...libraryItem,
        persistent_audio: { status: 'unavailable', filename: null, content_type: null },
      })))
    render(<LibraryPage onOpen={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))

    expect(await screen.findByText(/Persistent training audio is unavailable/)).toBeInTheDocument()
  })

  it('requires delete confirmation and leaves cancellation safe', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<LibraryPage onOpen={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Delete from Library' }))

    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
  })

  it('deletes only after confirmation and returns to the empty list', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<LibraryPage onOpen={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Delete from Library' }))

    expect(await screen.findByText(/Your Library is empty/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(true)
  })

  it('rejects a malformed detail response without opening it', async () => {
    const user = userEvent.setup()
    fetchMock.mockImplementation((url: string) => String(url).endsWith('/api/library/items')
      ? Promise.resolve(response({ items: [librarySummary] }))
      : Promise.resolve(response({ ...libraryItem, lesson: { id: 'broken' } })))
    render(<LibraryPage onOpen={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('could not be loaded')
  })

  it.each([
    ['backend failure', response({}, 500)],
    ['malformed response', response({ items: [{ title: 42 }] })],
  ])('shows a safe error for %s', async (_label, result) => {
    fetchMock.mockResolvedValue(result)
    render(<LibraryPage onOpen={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('My Library is unavailable')
  })
})
