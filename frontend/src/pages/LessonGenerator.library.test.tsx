import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createPersonalSession, savePersonalSession } from '../session/personalSession'
import { createTrainingState } from '../training/trainingMachine'
import { libraryItem } from '../testFixtures/libraryItem'
import { personalLesson } from '../testFixtures/personalLesson'
import LessonGenerator from './LessonGenerator'


const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

function successfulRoutes(url: string, options?: RequestInit) {
  const value = String(url)
  if (value.includes('/api/lesson/from-url')) return Promise.resolve(response(personalLesson))
  if (value.includes('/api/library/items')) {
    if (options?.method === 'PATCH') {
      return Promise.resolve(response({
        ...libraryItem,
        progress: JSON.parse(String(options.body)),
      }))
    }
    const request = options?.body ? JSON.parse(String(options.body)) : null
    return Promise.resolve(response({
      ...libraryItem,
      lesson: request?.lesson ?? libraryItem.lesson,
      progress: request?.progress ?? libraryItem.progress,
    }))
  }
  return Promise.resolve(response({ chunks: [] }))
}

describe('LessonGenerator Library integration', () => {
  beforeEach(() => {
    localStorage.clear()
    window.history.replaceState({}, '', '/')
    fetchMock.mockReset()
    fetchMock.mockImplementation(successfulRoutes)
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows a generated Lesson immediately and asynchronously saves it', async () => {
    const user = userEvent.setup()
    render(<LessonGenerator />)
    await user.type(screen.getByLabelText('YouTube URL'), personalLesson.source_url)
    await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))

    expect(await screen.findByRole('heading', { name: personalLesson.title })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith('/api/library/items'),
    )).toBe(true))
    const saveCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/library/items'))
    expect(JSON.parse(saveCall?.[1]?.body as string)).toMatchObject({
      lesson: { id: personalLesson.id },
      progress: { stage_index: 0, sentence_index: 0 },
    })
  })

  it('keeps the Lesson usable and shows a warning when Library save fails', async () => {
    fetchMock.mockImplementation((url: string) => String(url).includes('/lesson/from-url')
      ? Promise.resolve(response(personalLesson))
      : Promise.resolve(response({}, 500)))
    const user = userEvent.setup()
    render(<LessonGenerator />)
    await user.type(screen.getByLabelText('YouTube URL'), personalLesson.source_url)
    await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))

    expect(await screen.findByRole('heading', { name: personalLesson.title })).toBeInTheDocument()
    expect(await screen.findByText(/could not be saved to My Library/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Training' })).toBeEnabled()
  })

  it('retries a failed Library save without regenerating the Lesson', async () => {
    let libraryAttempts = 0
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/lesson/from-url')) return Promise.resolve(response(personalLesson))
      if (String(url).endsWith('/api/library/items')) {
        libraryAttempts += 1
        return Promise.resolve(libraryAttempts === 1 ? response({}, 500) : successfulRoutes(url, options))
      }
      return Promise.resolve(response({ chunks: [] }))
    })
    const user = userEvent.setup()
    render(<LessonGenerator />)
    await user.type(screen.getByLabelText('YouTube URL'), personalLesson.source_url)
    await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))
    await user.click(await screen.findByRole('button', { name: 'Retry save' }))

    await waitFor(() => expect(screen.queryByText(/could not be saved/)).not.toBeInTheDocument())
    expect(libraryAttempts).toBe(2)
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/lesson/from-url'))).toHaveLength(1)
  })

  it('idempotently ensures a restored Personal Session in the Library', async () => {
    savePersonalSession(createPersonalSession(personalLesson))
    render(<LessonGenerator />)

    expect(await screen.findByRole('heading', { name: personalLesson.title })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith('/api/library/items'),
    )).toHaveLength(1))
  })

  it('syncs canonical training boundaries without changing practice writes', async () => {
    savePersonalSession(createPersonalSession(personalLesson))
    const user = userEvent.setup()
    render(<LessonGenerator />)
    await screen.findByRole('heading', { name: personalLesson.title })
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith('/api/library/items'),
    )).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Start Training' }))

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) =>
      String(url).endsWith('/progress') && options?.method === 'PATCH',
    )).toBe(true))
    const patch = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/progress'))
    expect(JSON.parse(patch?.[1]?.body as string)).toMatchObject({
      is_training: true,
      stage_index: 0,
      training_order: personalLesson.training_order,
    })
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/practice'))).toBe(false)
  })

  it('persists a training change made while the initial Library save is pending', async () => {
    let finishSave: ((value: ReturnType<typeof response>) => void) | undefined
    const pendingSave = new Promise<ReturnType<typeof response>>((resolve) => {
      finishSave = resolve
    })
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/lesson/from-url')) return Promise.resolve(response(personalLesson))
      if (String(url).endsWith('/api/library/items')) return pendingSave
      return successfulRoutes(url, options)
    })
    const user = userEvent.setup()
    render(<LessonGenerator />)
    await user.type(screen.getByLabelText('YouTube URL'), personalLesson.source_url)
    await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))
    await user.click(await screen.findByRole('button', { name: 'Start Training' }))

    finishSave?.(response(libraryItem))

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) =>
      String(url).endsWith('/progress') && options?.method === 'PATCH' &&
      JSON.parse(String(options.body)).is_training === true,
    )).toBe(true))
  })

  it('does not write development stage overrides as Library progress', async () => {
    const canonical = {
      ...createTrainingState(personalLesson.training_order),
      stageIndex: 3,
      sentenceIndex: 2,
      completedStages: personalLesson.training_order.slice(0, 3),
    }
    savePersonalSession({
      ...createPersonalSession(personalLesson),
      isTraining: true,
      lessonMemoryImported: true,
      trainingState: canonical,
    })
    window.history.replaceState({}, '', '/?stage=shadowing')
    const user = userEvent.setup()
    render(<LessonGenerator />)
    expect(await screen.findByRole('heading', { name: 'Shadowing' })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith('/api/library/items'),
    )).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Blind Listening' }))

    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/progress'))).toBe(false)
  })

  it('does not create false progress merely by restoring a training session', async () => {
    savePersonalSession({
      ...createPersonalSession(personalLesson),
      isTraining: true,
      lessonMemoryImported: true,
      trainingState: {
        ...createTrainingState(personalLesson.training_order),
        stageIndex: 3,
        sentenceIndex: 2,
        completedStages: personalLesson.training_order.slice(0, 3),
      },
    })
    render(<LessonGenerator />)
    expect(await screen.findByRole('heading', { name: 'Mini Dictation' })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith('/api/library/items'),
    )).toBe(true))

    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/progress'))).toBe(false)
  })
})
