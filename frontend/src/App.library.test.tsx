import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import {
  createPersonalSession,
  loadPersonalSession,
  savePersonalSession,
} from './session/personalSession'
import { libraryItem, librarySummary } from './testFixtures/libraryItem'
import { personalLesson } from './testFixtures/personalLesson'


const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

function routeFetch(url: string, options?: RequestInit) {
  if (options?.method === 'DELETE') return Promise.resolve(response(null, 204))
  if (String(url).endsWith('/api/library/items')) {
    return Promise.resolve(response({ items: [librarySummary] }))
  }
  return Promise.resolve(response(libraryItem))
}

describe('App Library navigation', () => {
  beforeEach(() => {
    localStorage.clear()
    fetchMock.mockReset()
    fetchMock.mockImplementation(routeFetch)
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('restores the saved Lesson and canonical position into the Personal Session', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'My Library' }))
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Continue Training' }))

    expect(await screen.findByRole('heading', { name: 'Mini Dictation' })).toBeInTheDocument()
    expect(loadPersonalSession()).toMatchObject({
      lesson: { id: libraryItem.lesson.id },
      isTraining: true,
      trainingState: {
        order: libraryItem.lesson.training_order,
        stageIndex: 3,
        sentenceIndex: 2,
        completedStages: libraryItem.progress.completed_stages,
      },
    })
  })

  it('provides top-level Words & Chunks navigation without changing the active session', async () => {
    const user = userEvent.setup()
    savePersonalSession(createPersonalSession(personalLesson))
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'Words & Chunks' }))

    expect(await screen.findByRole('heading', { name: 'Words & Chunks' })).toBeInTheDocument()
    expect(loadPersonalSession()?.lesson.id).toBe(personalLesson.id)
  })

  it('provides top-level Listening navigation backed by Library summaries', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'Listening' }))

    expect(await screen.findByRole('heading', { name: 'Listening' })).toBeInTheDocument()
    expect(screen.getByText(librarySummary.title)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue Listening/ })).toBeInTheDocument()
    expect(document.querySelector('#language-ai-youtube-iframe-api')).toBeNull()
  })

  it('does not delete the active Personal Session when its Library item is deleted', async () => {
    const user = userEvent.setup()
    savePersonalSession(createPersonalSession(personalLesson))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'My Library' }))
    await user.click(await screen.findByRole('button', { name: /Daily lesson/ }))
    await user.click(await screen.findByRole('button', { name: 'Delete from Library' }))

    expect(await screen.findByText(/Your Library is empty/)).toBeInTheDocument()
    expect(loadPersonalSession()?.lesson.id).toBe(personalLesson.id)
  })
})
