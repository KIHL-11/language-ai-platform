import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LEARNER_ID_STORAGE_KEY } from '../session/learnerSession'
import { libraryItem } from '../testFixtures/libraryItem'
import { studyCandidates, studyItem } from '../testFixtures/studyBank'
import StudyCuration from './StudyCuration'


const LEARNER_ID = '123e4567-e89b-42d3-a456-426614174000'
const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

function routes(_url: string, options?: RequestInit) {
  if (options?.method === 'POST') {
    const request = JSON.parse(String(options.body))
    const items = request.items.map((item: typeof studyItem, index: number) => ({
      ...studyItem, ...item, id: `saved-${index}`, normalized_text: item.kind === 'word' ? 'figure' : 'figure it out',
    }))
    return Promise.resolve(response({ items, created: items.length }))
  }
  return Promise.resolve(response({ candidates: studyCandidates }))
}

describe('Study curation', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem(LEARNER_ID_STORAGE_KEY, LEARNER_ID)
    fetchMock.mockReset()
    fetchMock.mockImplementation(routes)
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows recommended words with source context and metadata by default', async () => {
    render(<StudyCuration item={libraryItem} onBack={vi.fn()} />)

    expect(await screen.findByText('figuring')).toBeInTheDocument()
    expect(screen.getByText(/Lemma: figure · POS: verb/)).toBeInTheDocument()
    expect(screen.getByText('We can figure it out.')).toBeInTheDocument()
    expect(screen.queryByText('figure it out')).not.toBeInTheDocument()
  })

  it('supports chunk recommended, review, and transparent rejected filters', async () => {
    const user = userEvent.setup()
    render(<StudyCuration item={libraryItem} onBack={vi.fn()} />)
    await screen.findByText('figuring')
    await user.click(screen.getByRole('button', { name: 'Chunks' }))
    expect(screen.getByText('figure it out')).toBeInTheDocument()
    expect(screen.queryByText('To go?')).not.toBeInTheDocument()
    expect(screen.queryByText('Ah Ah Ah')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Review' }))
    expect(screen.getByText('To go?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show rejected' }))
    expect(screen.getByText('Ah Ah Ah')).toBeInTheDocument()
    expect(screen.getByLabelText('Select Ah Ah Ah')).toBeDisabled()
  })

  it('saves one explicitly and marks repeated actions as saved', async () => {
    const user = userEvent.setup()
    render(<StudyCuration item={libraryItem} onBack={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  })

  it('bulk saves selected candidates in one request', async () => {
    const user = userEvent.setup()
    render(<StudyCuration item={libraryItem} onBack={vi.fn()} />)
    await screen.findByText('figuring')
    await user.click(screen.getByLabelText('Select figuring'))
    await user.click(screen.getByRole('button', { name: /Save Selected/ }))

    await waitFor(() => expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1))
    const request = fetchMock.mock.calls.find(([, options]) => options?.method === 'POST')
    expect(JSON.parse(String(request?.[1]?.body)).items).toHaveLength(1)
  })

  it('shows a contained error when candidate loading fails', async () => {
    fetchMock.mockResolvedValue(response({}, 500))
    render(<StudyCuration item={libraryItem} onBack={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be loaded')
    expect(screen.getByRole('button', { name: 'Back to Lesson' })).toBeEnabled()
  })
})
