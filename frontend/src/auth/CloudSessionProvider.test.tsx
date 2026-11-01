import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cloudAuth } from './client'
import { CloudSessionProvider } from './CloudSessionProvider'
import { librarySummary } from '../testFixtures/libraryItem'
import { deleteLibraryItem } from '../api/library'
import { deleteStudyItem } from '../api/studyBank'
import { rateReviewItem } from '../api/review'
import { generateChunkSuggestions } from '../api/aiChunks'
import { previewTopic } from '../api/topics'

vi.mock('../api/apiBase', () => ({ API_BASE_URL: '' }))
const json = (value: unknown, status = 200, owner = 'owner-a') => new Response(JSON.stringify(value), { status, headers: { 'X-Authenticated-User': owner } })

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('Cloud session integration', () => {
  function routes(owner = 'owner-a') {
    vi.stubGlobal('location', { href: 'https://learn.example/', origin: 'https://learn.example', protocol: 'https:' })
    const mock = vi.fn((url: string) => {
      if (url === '/api/deployment') return Promise.resolve(json({ mode: 'cloud' }))
      if (url === '/api/account/me') return Promise.resolve(json({ user_id: owner, csrf_token: 'a'.repeat(64) }))
      if (url === '/api/library/items') return Promise.resolve(json({ items: [{ ...librarySummary, title: `${owner} private title` }] }))
      if (String(url).includes('/study-bank/items')) return Promise.resolve(json({ items: [] }))
      return Promise.resolve(json({}))
    })
    vi.stubGlobal('fetch', mock)
    return mock
  }

  it('never mounts Personal Desktop in cloud and clears private views on account change', async () => {
    const mock = routes()
    const personal = vi.fn(() => <p>Personal records</p>)
    function Personal() { return personal() }
    const user = userEvent.setup()
    cloudAuth.invalidate()
    render(<CloudSessionProvider><Personal /></CloudSessionProvider>)
    await screen.findByRole('heading', { name: 'Words & Chunks' })
    expect(personal).not.toHaveBeenCalled()
    expect(mock.mock.calls.some(([url]) => String(url).includes('user_id=owner-a'))).toBe(true)
    await user.click(screen.getByRole('button', { name: 'My Library' }))
    expect(await screen.findByText('owner-a private title')).toBeInTheDocument()
    mock.mockImplementation((url: string) => url === '/api/account/me' ? Promise.resolve(json({ user_id: 'owner-b', csrf_token: 'b'.repeat(64) }, 200, 'owner-b')) : Promise.resolve(json({ items: [] }, 200, 'owner-b')))
    await act(() => cloudAuth.refresh())
    expect(screen.queryByText('owner-a private title')).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Words & Chunks' })).toBeInTheDocument()
    expect(personal).not.toHaveBeenCalled()
  })

  it('keeps missing sessions gated and public login unavailable', async () => {
    const mock = routes()
    mock.mockImplementation((url: string) => Promise.resolve(url === '/api/deployment' ? json({ mode: 'cloud' }) : json({}, 401)))
    render(<CloudSessionProvider><p>Personal data must not mount</p></CloudSessionProvider>)
    expect(await screen.findByText('Your session is missing or expired.')).toBeInTheDocument()
    expect(screen.queryByText('Personal data must not mount')).not.toBeInTheDocument()
    expect(screen.getByText(/Public login is unavailable/)).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('uses CSRF on every migrated mutating API and rejects stale owner claims locally', async () => {
    const mock = routes()
    await cloudAuth.bootstrap('')
    mock.mockImplementation(() => Promise.resolve(json({}, 503)))
    await Promise.allSettled([
      deleteLibraryItem('library-one'), deleteStudyItem('owner-a', 'study-one'),
      rateReviewItem('owner-a', 'study-one', 'good', 0),
      generateChunkSuggestions('owner-a', 'en', 'Bounded learning text'), previewTopic('en-friends-reunion', 'owner-a'),
    ])
    const writes = mock.mock.calls.slice(2)
    expect(writes).toHaveLength(5)
    writes.forEach((call) => {
      const init = (call as unknown as [string, RequestInit])[1]
      expect(init.credentials).toBe('same-origin')
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('a'.repeat(64))
    })
    const count = mock.mock.calls.length
    await expect(deleteStudyItem('owner-b', 'study-other')).rejects.toMatchObject({ status: 409 })
    expect(mock).toHaveBeenCalledTimes(count)
    await waitFor(() => expect(cloudAuth.getSnapshot()).toMatchObject({ userId: 'owner-a' }))
  })
})
