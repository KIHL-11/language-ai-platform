import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import LessonGenerator from './LessonGenerator'
import type { MediaCandidate } from '../types/mediaDiscovery'
import type { Lesson } from '../types/lesson'

const candidate: MediaCandidate = {
  provider: 'youtube', provider_id: 'video', url: 'https://www.youtube.com/watch?v=canonical',
  title: 'Everyday science', channel: 'Science channel', duration_seconds: 125,
  thumbnail: 'https://i.ytimg.com/vi/video/default.jpg', is_live: false, availability: 'public',
  subtitle_tracks: [{ language: 'en', has_human: true, has_automatic: false },
    { language: 'de', has_human: false, has_automatic: true }],
  suitability: { total: 90, reasons: ['Human subtitles available', 'Good duration', 'Public video'] },
}
const lesson: Lesson = {
  id: 'generated', title: 'Generated science lesson', source_url: candidate.url,
  source_language: 'en', target_language: 'zh-CN', level: 'B2', audio_url: null,
  source: 'subtitles', sentences: [], training_order: ['live_dialogue'],
  training_plan: { active_recall: { enabled: false, count: 0 }, blind_listening: { enabled: false },
    comprehension: { enabled: false }, mini_dictation: { enabled: false }, shadowing: { enabled: false },
    retell: { enabled: false }, live_dialogue: { enabled: true } },
  ai: { provider: 'mock', status: 'complete' },
}
const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body })
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); window.history.replaceState({}, '', '/') })
afterEach(() => vi.unstubAllGlobals())

async function openSearch() {
  const user = userEvent.setup()
  render(<LessonGenerator />)
  await user.click(screen.getByRole('button', { name: 'Find a Video for Me' }))
  await user.type(screen.getByLabelText('Search topic'), 'science')
  return user
}

it('preserves URL generation and training navigation', async () => {
  fetchMock.mockResolvedValue(response(lesson))
  const user = userEvent.setup()
  render(<LessonGenerator />)
  await user.type(screen.getByLabelText('YouTube URL'), candidate.url)
  await user.click(screen.getByRole('button', { name: 'Generate Lesson' }))
  expect(JSON.parse(fetchMock.mock.calls[0][1].body).url).toBe(candidate.url)
  await user.click(await screen.findByRole('button', { name: 'Start Training' }))
  expect(screen.getByRole('heading', { name: 'Live Dialogue', level: 1 })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Back to lesson' }))
  expect(screen.getByRole('heading', { name: lesson.title })).toBeInTheDocument()
})

it('submits the backend contract and renders ranked captions without creating a lesson', async () => {
  fetchMock.mockResolvedValue(response([candidate]))
  const user = await openSearch()
  await user.selectOptions(screen.getByLabelText('Source language'), 'de')
  await user.type(screen.getByLabelText('Search topic'), 'science')
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/media\/discover$/)
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ query: 'science', target_language: 'de', min_duration_seconds: 60, max_duration_seconds: 1800, limit: 5 })
  expect(await screen.findByText(candidate.title)).toBeInTheDocument()
  expect(screen.getByText('Science channel · 2:05')).toBeInTheDocument()
  expect(screen.getByText('en: Human subtitles')).toBeInTheDocument()
  expect(screen.getByText('de: Automatic captions')).toBeInTheDocument()
  expect(screen.getByText('Suitability score: 90/100')).toBeInTheDocument()
  for (const reason of candidate.suitability.reasons) expect(screen.getByText(reason)).toBeInTheDocument()
  expect(document.querySelector('img')).toHaveAttribute('alt', '')
})

it('shows loading and prevents duplicate discovery submissions', async () => {
  let resolve!: (value: unknown) => void
  fetchMock.mockReturnValue(new Promise(r => { resolve = r }))
  const user = await openSearch()
  const button = screen.getByRole('button', { name: 'Find videos' })
  fireEvent.click(button); fireEvent.click(button)
  expect(screen.getByText('Searching for videos...')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await act(async () => resolve(response([])))
  expect(screen.getByText(/No suitable videos found/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Paste Video URL' }))
  expect(screen.getByLabelText('YouTube URL')).toBeInTheDocument()
})

it.each([502, 422, 500])('handles HTTP %s without exposing provider details', async status => {
  fetchMock.mockResolvedValue(response({ detail: 'SECRET traceback provider exception' }, status))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(status === 502 ? 'temporarily unavailable' : status === 422 ? 'Check your search' : 'Video search failed')
  expect(screen.queryByText(/SECRET/)).not.toBeInTheDocument()
})

it('handles network failures safely', async () => {
  fetchMock.mockRejectedValue(new Error('Internal hostname secret'))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Video search failed')
})

it('validates duration ordering before calling the API', async () => {
  const user = await openSearch()
  await user.clear(screen.getByLabelText('Maximum duration (seconds)'))
  await user.type(screen.getByLabelText('Maximum duration (seconds)'), '30')
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(screen.getByRole('alert')).toHaveTextContent('valid duration range')
  expect(fetchMock).not.toHaveBeenCalled()
})

it('uses canonical URL and current lesson configuration once on rapid selection', async () => {
  let resolve!: (value: unknown) => void
  fetchMock.mockResolvedValueOnce(response([candidate])).mockImplementationOnce(() => new Promise(r => { resolve = r }))
  const user = await openSearch()
  await user.selectOptions(screen.getByLabelText('Level'), 'C1')
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  const button = await screen.findByRole('button', { name: `Use this video: ${candidate.title}` })
  fireEvent.click(button); fireEvent.click(button)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/lesson\/from-url$/)
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ url: candidate.url, source_language: 'en', target_language: 'zh-CN', level: 'C1', vocals: false })
  await act(async () => resolve(response(lesson)))
  expect(screen.getByRole('heading', { name: lesson.title })).toBeInTheDocument()
})

it('clears stale results when search inputs or language change', async () => {
  fetchMock.mockResolvedValue(response([candidate]))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  await screen.findByText(candidate.title)
  await user.type(screen.getByLabelText('Search topic'), ' new')
  expect(screen.queryByText(candidate.title)).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Source language'), 'de')
  expect(screen.getByLabelText('Search topic')).toHaveValue('')
})

it('ignores canceled search responses arriving after a newer search', async () => {
  let resolve!: (value: unknown) => void
  fetchMock.mockImplementationOnce(() => new Promise(r => { resolve = r })).mockResolvedValueOnce(response([]))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  await user.type(screen.getByLabelText('Search topic'), ' new')
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  await waitFor(() => expect(screen.getByText(/No suitable videos found/)).toBeInTheDocument())
  await act(async () => resolve(response([candidate])))
  expect(screen.queryByText(candidate.title)).not.toBeInTheDocument()
})

it('rejects unsafe video and thumbnail URLs and renders metadata as text', async () => {
  fetchMock.mockResolvedValue(response([{ ...candidate, title: '<script>alert(1)</script>', url: 'https://youtube.com.evil.test/watch', thumbnail: 'javascript:alert(1)' }]))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(await screen.findByText('<script>alert(1)</script>')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Use this video:/ })).toBeDisabled()
  expect(document.querySelector('img')).toBeNull()
  expect(document.querySelector('script')).toBeNull()
})

it('maps custom duration and result count fields directly to the request', async () => {
  fetchMock.mockResolvedValue(response([]))
  const user = await openSearch()
  for (const [label, value] of [['Minimum duration (seconds)', '120'], ['Maximum duration (seconds)', '600'], ['Number of results', '3']]) {
    await user.clear(screen.getByLabelText(label))
    await user.type(screen.getByLabelText(label), value)
  }
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ min_duration_seconds: 120, max_duration_seconds: 600, limit: 3 })
})

it('cancels discovery when switching back to URL mode without generating a lesson', async () => {
  fetchMock.mockReturnValue(new Promise(() => {}))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  await user.click(screen.getByRole('button', { name: 'Paste Video URL' }))
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('handles malformed provider responses as a friendly error', async () => {
  fetchMock.mockResolvedValue(response([{ title: 'incomplete' }]))
  const user = await openSearch()
  await user.click(screen.getByRole('button', { name: 'Find videos' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('unexpected response')
})
