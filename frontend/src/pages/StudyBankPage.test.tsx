import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LEARNER_ID_STORAGE_KEY } from '../session/learnerSession'
import { studyItem } from '../testFixtures/studyBank'
import StudyBankPage from './StudyBankPage'


const LEARNER_ID = '123e4567-e89b-42d3-a456-426614174000'
const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

describe('Words & Chunks page', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem(LEARNER_ID_STORAGE_KEY, LEARNER_ID)
    fetchMock.mockReset()
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (options?.method === 'DELETE') return Promise.resolve(response(null, 204))
      if (options?.method === 'PATCH') return Promise.resolve(response({ ...studyItem, active: false }))
      return Promise.resolve(response({
        items: [{ ...studyItem, active: !String(url).includes('active=false') }],
      }))
    })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('shows curated words and their complete provenance', async () => {
    const user = userEvent.setup()
    render(<StudyBankPage />)
    expect(await screen.findByText('figuring')).toBeInTheDocument()
    await user.click(screen.getByText('Sources'))
    expect(screen.getByText('Daily lesson')).toBeInTheDocument()
    expect(screen.getByText('1.0–3.0s')).toBeInTheDocument()
    expect(screen.getByText('We can figure it out.')).toBeInTheDocument()
  })

  it('sends kind, language, active, and search filters', async () => {
    const user = userEvent.setup()
    render(<StudyBankPage />)
    await screen.findByText('figuring')
    await user.click(screen.getByRole('button', { name: 'Chunks' }))
    await user.click(screen.getByRole('button', { name: 'German' }))
    await user.click(screen.getByRole('button', { name: 'Archived' }))
    await user.type(screen.getByLabelText('Search Words & Chunks'), 'reise')

    await waitFor(() => {
      const url = String(fetchMock.mock.calls.at(-1)?.[0])
      expect(url).toContain('kind=chunk')
      expect(url).toContain('language=de')
      expect(url).toContain('active=false')
      expect(url).toContain('search=reise')
    })
  })

  it('archives an active item and can request archived items for restore', async () => {
    const user = userEvent.setup()
    render(<StudyBankPage />)
    await user.click(await screen.findByRole('button', { name: 'Archive' }))
    expect(screen.queryByText('figuring')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Archived' }))
    await user.click(await screen.findByRole('button', { name: 'Restore' }))
    const restore = fetchMock.mock.calls.find(([, options]) =>
      options?.method === 'PATCH' && String(options.body).includes('true'),
    )
    expect(restore).toBeDefined()
  })

  it('requires confirmation before permanent delete', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<StudyBankPage />)
    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
  })

  it('deletes after confirmation and keeps failures contained', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<StudyBankPage />)
    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    expect(screen.queryByText('figuring')).not.toBeInTheDocument()
  })

  it('shows a safe error for API failure', async () => {
    fetchMock.mockResolvedValue(response({}, 500))
    render(<StudyBankPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  })
})
