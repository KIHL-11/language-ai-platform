import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  PERSONAL_SESSION_STORAGE_KEY,
  createPersonalSession,
  loadPersonalSession,
  savePersonalSession,
} from '../session/personalSession'
import { createTrainingState } from '../training/trainingMachine'
import type { TrainingSessionState } from '../training/trainingMachine'
import { personalLesson } from '../testFixtures/personalLesson'
import { libraryItem } from '../testFixtures/libraryItem'
import LessonGenerator from './LessonGenerator'


const fetchMock = vi.fn()
const response = (body: unknown) => ({
  ok: true,
  status: 200,
  json: async () => body,
})

function stateAt(
  stageIndex: number,
  sentenceIndex = 0,
): TrainingSessionState {
  return {
    ...createTrainingState(personalLesson.training_order),
    stageIndex,
    sentenceIndex,
    completedStages: personalLesson.training_order.slice(0, stageIndex),
  }
}

function storeTrainingSession(state: TrainingSessionState) {
  savePersonalSession({
    ...createPersonalSession(personalLesson),
    isTraining: true,
    lessonMemoryImported: true,
    trainingState: state,
  })
}

describe('LessonGenerator personal session restore', () => {
  beforeEach(() => {
    localStorage.clear()
    window.history.replaceState({}, '', '/')
    fetchMock.mockReset()
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/library/items')) return response(libraryItem)
      return response({ chunks: [] })
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('restores a valid saved lesson and ensures it in the Library', async () => {
    savePersonalSession(createPersonalSession(personalLesson))

    render(<LessonGenerator />)

    expect(screen.getByRole('heading', { name: personalLesson.title })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Training' })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][0]).toContain('/api/library/items')
  })

  it.each([
    [0, 0, 'Active Recall'],
    [1, 0, 'Blind Listening'],
    [3, 2, 'Mini Dictation'],
  ] as const)(
    'restores stage %s sentence %s as %s',
    async (stageIndex, sentenceIndex, heading) => {
      storeTrainingSession(stateAt(stageIndex, sentenceIndex))

      render(<LessonGenerator />)

      expect(await screen.findByRole('heading', { name: heading, level: 1 })).toBeInTheDocument()
      if (stageIndex === 3) {
        expect(screen.getAllByText('Sentence 3 of 3').length).toBeGreaterThan(0)
      }
    },
  )

  it('restores a backend-relative audio URL without storing resolved or binary audio', () => {
    savePersonalSession(createPersonalSession(personalLesson))

    render(<LessonGenerator />)

    expect(screen.getByRole('region', { name: 'Lesson audio' }).querySelector('audio')).toHaveAttribute(
      'src',
      'http://127.0.0.1:8000/media/personal.mp3',
    )
  })

  it('replaces the prior session only after a different lesson succeeds', async () => {
    const user = userEvent.setup()
    const nextLesson = {
      ...personalLesson,
      id: 'replacement',
      title: 'Replacement lesson',
      source_url: 'https://www.youtube.com/watch?v=replacement',
    }
    savePersonalSession(createPersonalSession(personalLesson))
    fetchMock.mockImplementation(async (requestUrl: string) =>
      String(requestUrl).includes('/api/lesson/from-url')
        ? response(nextLesson)
        : response({ ...libraryItem, lesson: nextLesson }),
    )
    render(<LessonGenerator />)

    await user.clear(screen.getByLabelText('YouTube URL'))
    await user.type(screen.getByLabelText('YouTube URL'), nextLesson.source_url)
    await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))

    expect(await screen.findByRole('heading', { name: nextLesson.title })).toBeInTheDocument()
    expect(loadPersonalSession()).toMatchObject({
      lesson: { id: 'replacement' },
      isTraining: false,
      lessonMemoryImported: false,
      trainingState: { stageIndex: 0, sentenceIndex: 0 },
    })
  })

  it('retains the saved lesson when replacement generation fails', async () => {
    const user = userEvent.setup()
    savePersonalSession(createPersonalSession(personalLesson))
    fetchMock.mockImplementation(async (requestUrl: string) => {
      if (String(requestUrl).includes('/api/lesson/from-url')) {
        throw new Error('temporarily offline')
      }
      return response(libraryItem)
    })
    render(<LessonGenerator />)

    await user.clear(screen.getByLabelText('YouTube URL'))
    await user.type(screen.getByLabelText('YouTube URL'), 'https://www.youtube.com/watch?v=failed')
    await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))

    expect(await screen.findByText('Lesson generation failed. Please try again.')).toBeInTheDocument()
    expect(loadPersonalSession()?.lesson.id).toBe(personalLesson.id)
    expect(screen.getByRole('heading', { name: personalLesson.title })).toBeInTheDocument()
  })

  it('persists restart session at Active Recall without changing stage order', async () => {
    const user = userEvent.setup()
    storeTrainingSession(stateAt(3, 2))
    render(<LessonGenerator />)

    await user.click(screen.getByRole('button', { name: 'Restart session' }))

    await waitFor(() => {
      expect(loadPersonalSession()?.trainingState).toMatchObject({
        order: personalLesson.training_order,
        stageIndex: 0,
        sentenceIndex: 0,
        completedStages: [],
      })
    })
    expect(await screen.findByRole('heading', { name: 'Active Recall', level: 1 })).toBeInTheDocument()
  })

  it('keeps a development stage override out of canonical persisted progression', async () => {
    const canonical = stateAt(3, 2)
    storeTrainingSession(canonical)
    window.history.replaceState({}, '', '/?stage=shadowing')
    const user = userEvent.setup()
    render(<LessonGenerator />)

    expect(screen.getByRole('heading', { name: 'Shadowing', level: 1 })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Blind Listening' }))

    expect(loadPersonalSession()?.trainingState).toEqual(canonical)
  })

  it('does not re-import lesson memory or write practice merely because it restored', async () => {
    storeTrainingSession(stateAt(0))

    render(<LessonGenerator />)

    await screen.findByText('No curated chunks yet. Save useful chunks from your Library first.')
    const requests = fetchMock.mock.calls.map(([url, options]) => ({
      url: String(url),
      method: options?.method ?? 'GET',
    }))
    expect(requests).toHaveLength(2)
    expect(requests.some((request) => request.method === 'POST' && request.url.includes('/api/library/items'))).toBe(true)
    expect(requests.some((request) => request.method === 'GET' && request.url.includes('/active-recall'))).toBe(true)
    expect(requests.every((request) => !request.url.includes('/from-lesson'))).toBe(true)
    expect(requests.every((request) => !request.url.includes('/practice'))).toBe(true)
  })

  it('ignores invalid storage and returns to lesson generation safely', () => {
    localStorage.setItem(PERSONAL_SESSION_STORAGE_KEY, '{invalid')

    render(<LessonGenerator />)

    expect(screen.getByRole('heading', { name: 'Generate a lesson' })).toBeInTheDocument()
    expect(screen.queryByText(personalLesson.title)).not.toBeInTheDocument()
  })
})
