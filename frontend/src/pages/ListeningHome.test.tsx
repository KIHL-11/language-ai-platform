import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { libraryItem, librarySummary } from '../testFixtures/libraryItem'
import ListeningHome from './ListeningHome'

const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

describe('Listening Home', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    fetchMock.mockImplementation((url: string) => String(url).endsWith('/api/library/items')
      ? Promise.resolve(response({ items: [librarySummary] }))
      : Promise.resolve(response(libraryItem)))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('loads Library summaries and shows continue position', async () => {
    render(<ListeningHome onOpen={vi.fn()} />)
    expect(await screen.findByText(librarySummary.title)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue Listening at 0:01' })).toBeInTheDocument()
  })

  it('filters language and searches title or source', async () => {
    const user = userEvent.setup()
    const german = { ...librarySummary, id: 'de', lesson_id: 'de', title: 'Berlin', source_url: 'https://example.test/berlin', source_language: 'de' as const }
    fetchMock.mockResolvedValue(response({ items: [librarySummary, german] }))
    render(<ListeningHome onOpen={vi.fn()} />)
    await screen.findByText('Berlin')
    await user.click(screen.getByRole('button', { name: 'German' }))
    expect(screen.queryByText(librarySummary.title)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'All' }))
    await user.type(screen.getByLabelText('Search Listening media'), 'youtube.com')
    expect(screen.getByText(librarySummary.title)).toBeInTheDocument()
    expect(screen.queryByText('Berlin')).not.toBeInTheDocument()
  })

  it('opens a full saved item without regenerating a Lesson', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(<ListeningHome onOpen={onOpen} />)
    await user.click(await screen.findByRole('button', { name: /Continue Listening/ }))
    expect(onOpen).toHaveBeenCalledWith(libraryItem)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/lesson/from-url'))).toBe(false)
  })

  it('degrades to clear empty and unavailable states', async () => {
    fetchMock.mockResolvedValue(response({ items: [] }))
    const view = render(<ListeningHome onOpen={vi.fn()} />)
    expect(await screen.findByText(/No saved media yet/)).toBeInTheDocument()
    view.unmount()
    fetchMock.mockRejectedValue(new Error('offline'))
    render(<ListeningHome onOpen={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Listening Library is unavailable')
  })
})
