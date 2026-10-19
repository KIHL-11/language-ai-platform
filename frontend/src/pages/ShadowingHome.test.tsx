import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { libraryItem, librarySummary } from '../testFixtures/libraryItem'
import ShadowingHome from './ShadowingHome'

const fetchMock = vi.fn()
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body })

describe('Shadowing Home', () => {
  beforeEach(() => {
    fetchMock.mockImplementation((url: string) => String(url).endsWith('/api/library/items')
      ? Promise.resolve(response({ items: [librarySummary, {
        ...librarySummary, id: 'de-item', title: 'Paris auf Deutsch', source_language: 'de',
        shadowing_attempt_count: 4, shadowing_target_sentence_count: 2,
      }] }))
      : Promise.resolve(response(libraryItem)))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('lists Library media, filters by language/search, and opens one shared workspace item', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(<ShadowingHome onOpen={onOpen} />)
    expect(await screen.findByText(librarySummary.title)).toBeInTheDocument()
    expect(screen.getByText('Paris auf Deutsch')).toBeInTheDocument()
    expect(screen.getByText(/Attempts: 4/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'German' }))
    expect(screen.queryByText(librarySummary.title)).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Search Shadowing media'), 'Paris')
    await user.click(screen.getByRole('button', { name: 'Continue Shadowing' }))
    expect(onOpen).toHaveBeenCalledWith(libraryItem)
  })
})
