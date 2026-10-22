import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MediaController, MediaControllerStatus } from '../listening/mediaController'
import type { ListeningControllerFactory } from '../listening/createController'
import { libraryItem } from '../testFixtures/libraryItem'
import { studyCandidates, studyItem } from '../testFixtures/studyBank'
import ListeningWorkspace from './ListeningWorkspace'

class FakeController implements MediaController {
  time = libraryItem.listening_progress.last_position_seconds
  duration = 3
  rate = 1
  status: MediaControllerStatus = 'ready'
  play = vi.fn(async () => undefined)
  pause = vi.fn()
  seek = vi.fn((seconds: number) => { this.time = seconds })
  getCurrentTime = vi.fn(() => this.time)
  getDuration = vi.fn(() => this.duration)
  setPlaybackRate = vi.fn((rate: number) => { this.rate = rate; return rate })
  getPlaybackRate = vi.fn(() => this.rate)
  getStatus = vi.fn(() => this.status)
  destroy = vi.fn()
}

const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

function fetchRoutes(url: string, options?: RequestInit) {
  const value = String(url)
  if (value.includes('/study-candidates')) return Promise.resolve(response({ candidates: studyCandidates }))
  if (value.includes('/study-bank/items/bulk')) return Promise.resolve(response({ items: [studyItem] }))
  if (value.includes('/listening-progress') && options?.method === 'PATCH') {
    return Promise.resolve(response({
      ...libraryItem,
      listening_progress: JSON.parse(String(options.body)),
    }))
  }
  return Promise.resolve(response({}))
}

function setup(result: Partial<{ kind: 'youtube' | 'audio' | 'transcript'; message: string | null; controller: MediaController | null }> = {}) {
  const fake = result.controller === undefined ? new FakeController() : result.controller
  const factory: ListeningControllerFactory = vi.fn().mockResolvedValue({
    controller: fake,
    kind: result.kind ?? 'youtube',
    message: result.message ?? null,
  })
  render(<ListeningWorkspace item={libraryItem} onBack={vi.fn()} controllerFactory={factory} />)
  return { fake: fake as FakeController | null, factory }
}

describe('Listening Workspace', () => {
  beforeEach(() => {
    localStorage.clear()
    fetchMock.mockReset()
    fetchMock.mockImplementation(fetchRoutes)
    vi.stubGlobal('fetch', fetchMock)
    Element.prototype.scrollIntoView = vi.fn()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('renders media, synchronized transcript, restored sentence, and settings', async () => {
    setup()
    expect(screen.getByRole('heading', { name: libraryItem.lesson.title })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Synchronized transcript' })).toBeInTheDocument()
    expect(screen.getByText(/CURRENT SENTENCE 02 \/ 03/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Auto-follow ON' })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
  })

  it('clicking transcript seeks without forcing autoplay and resets dictation attempt', async () => {
    const user = userEvent.setup()
    const { fake } = setup()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Dictation' }))
    await user.type(screen.getByLabelText('Write what you hear'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await user.click(screen.getByRole('button', { name: /Sentence 3\./ }))
    expect(fake?.seek).toHaveBeenCalledWith(2)
    expect(fake?.play).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Write what you hear')).toHaveValue('')
  })

  it('updates the current sentence from one clock interval and cleans it up', async () => {
    vi.useFakeTimers()
    const setIntervalSpy = vi.spyOn(window, 'setInterval')
    const { fake } = setup()
    await act(async () => { await Promise.resolve() })
    expect(setIntervalSpy).toHaveBeenCalledTimes(1)
    if (fake) fake.time = 2.2
    act(() => vi.advanceTimersByTime(250))
    expect(screen.getByText(/CURRENT SENTENCE 03 \/ 03/)).toBeInTheDocument()
  })

  it('auto-follows only sentence changes and can be disabled', async () => {
    const user = userEvent.setup()
    setup()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>
    const initialCalls = scroll.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Auto-follow ON' }))
    await user.click(screen.getByRole('button', { name: /Sentence 3\./ }))
    expect(scroll).toHaveBeenCalledTimes(initialCalls)
    expect(screen.getByRole('button', { name: 'Auto-follow OFF' })).toBeInTheDocument()
  })

  it('supports play, pause, relative seek, and actual playback speed', async () => {
    const user = userEvent.setup()
    const { fake } = setup()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(fake?.play).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    expect(fake?.pause).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: '-5 seconds' }))
    expect(fake?.seek).toHaveBeenLastCalledWith(0)
    await user.click(screen.getByRole('button', { name: '+5 seconds' }))
    expect(fake?.seek).toHaveBeenLastCalledWith(3)
    await user.click(screen.getByRole('button', { name: '0.75x' }))
    expect(fake?.setPlaybackRate).toHaveBeenCalledWith(0.75)
  })

  it('uses one exclusive sentence/custom loop and prevents invalid A-B', async () => {
    const user = userEvent.setup()
    const { fake } = setup()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Repeat sentence' }))
    expect(screen.getByRole('button', { name: 'Repeat sentence' })).toHaveAttribute('aria-pressed', 'true')
    if (fake) fake.time = 1
    await user.click(screen.getByRole('button', { name: 'Set A' }))
    expect(screen.getByRole('button', { name: 'Repeat sentence' })).toHaveAttribute('aria-pressed', 'false')
    if (fake) fake.time = 1.2
    await user.click(screen.getByRole('button', { name: 'Set B' }))
    expect(screen.getByRole('alert')).toHaveTextContent('at least 0.5 seconds')
    if (fake) fake.time = 2
    await user.click(screen.getByRole('button', { name: 'Set B' }))
    expect(screen.getByText(/A 0:01 · B 0:02/)).toBeInTheDocument()
  })

  it('updates sentence-loop boundaries when the selected sentence changes', async () => {
    vi.useFakeTimers()
    const { fake } = setup()
    await act(async () => { await Promise.resolve() })
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(screen.getByRole('button', { name: 'Repeat sentence' }))
    if (fake) fake.time = 1.99
    act(() => vi.advanceTimersByTime(250))
    expect(fake?.seek).toHaveBeenLastCalledWith(1)

    fireEvent.click(screen.getByRole('button', { name: /Sentence 3\./ }))
    if (fake) fake.time = 2.99
    act(() => vi.advanceTimersByTime(250))
    expect(fake?.seek).toHaveBeenLastCalledWith(2)
  })

  it('keeps display preferences while Blind reveal is transient', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(screen.getByRole('button', { name: 'Show translation' }))
    await user.click(screen.getByRole('button', { name: 'Blind' }))
    expect(screen.getAllByText('Source text hidden').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Reveal text' }))
    expect(screen.getAllByText('Sentence 2.').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Translation hidden').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Listen' }))
    expect(screen.getByRole('button', { name: 'Show translation' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('reuses deterministic dictation comparison and keyboard does not hijack its input', async () => {
    const user = userEvent.setup()
    const { fake } = setup()
    await user.click(screen.getByRole('button', { name: 'Dictation' }))
    const input = screen.getByLabelText('Write what you hear')
    await user.type(input, 'Sentence 2.')
    fireEvent.keyDown(input, { code: 'Space', key: ' ' })
    expect(fake?.play).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Check' }))
    expect(screen.getByText('Accuracy 100%')).toBeInTheDocument()
  })

  it('shows current-sentence Words and Chunks, hides rejected, and saves immediately', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(screen.getByRole('button', { name: /Sentence 1\./ }))
    expect(await screen.findByText('figuring')).toBeInTheDocument()
    expect(screen.getByText('figure it out')).toBeInTheDocument()
    expect(screen.queryByText('Ah Ah Ah')).not.toBeInTheDocument()
    const saveButtons = screen.getAllByRole('button', { name: 'Save' })
    await user.click(saveButtons[0])
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('opens the shared Sync Shadowing workspace at the current sentence', async () => {
    const user = userEvent.setup()
    const onShadowing = vi.fn()
    const fake = new FakeController()
    const factory: ListeningControllerFactory = vi.fn().mockResolvedValue({
      controller: fake, kind: 'audio', message: null,
    })
    render(<ListeningWorkspace item={libraryItem} onBack={vi.fn()} onShadowing={onShadowing} controllerFactory={factory} />)
    await user.click(await screen.findByRole('button', { name: 'Practice Sync Shadowing' }))
    expect(onShadowing).toHaveBeenCalledWith(2)
  })

  it('uses cleaned practiceable sentences for display, navigation, and shadowing entry', async () => {
    const user = userEvent.setup()
    const onShadowing = vi.fn()
    const item = {
      ...libraryItem,
      lesson: {
        ...libraryItem.lesson,
        sentences: libraryItem.lesson.sentences.map((sentence) => sentence.id === 2
          ? { ...sentence, text: '[music] [APPLAUSE]' }
          : sentence.id === 3 ? { ...sentence, text: 'Keep [SINGING] going.' } : sentence),
      },
    }
    const factory: ListeningControllerFactory = vi.fn().mockResolvedValue({
      controller: new FakeController(), kind: 'audio', message: null,
    })
    render(<ListeningWorkspace item={item} onBack={vi.fn()} onShadowing={onShadowing} controllerFactory={factory} />)
    expect(screen.getByText(/CURRENT SENTENCE 02 \/ 02/)).toBeInTheDocument()
    expect(screen.getAllByText('Keep going.').length).toBeGreaterThan(0)
    expect(screen.queryByText('[music] [APPLAUSE]')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Practice Sync Shadowing' }))
    expect(onShadowing).toHaveBeenCalledWith(3)
  })

  it('keeps listening usable when candidates fail', async () => {
    fetchMock.mockImplementation((url: string, options?: RequestInit) => String(url).includes('/study-candidates')
      ? Promise.reject(new Error('offline'))
      : fetchRoutes(url, options))
    setup()
    expect(await screen.findByRole('alert')).toHaveTextContent('Listening still works')
    expect(screen.getByRole('heading', { name: 'Synchronized transcript' })).toBeInTheDocument()
  })

  it.each([
    ['audio', 'Video unavailable. Using saved audio.'],
    ['transcript', 'No playable media is available. Transcript-only mode is active.'],
  ] as const)('degrades to %s mode without crashing', async (kind, message) => {
    setup({ kind, message, controller: kind === 'audio' ? new FakeController() : null })
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Synchronized transcript' })).toBeInTheDocument()
  })

  it('switches a failed provider to a supplied saved-audio fallback', async () => {
    const youtube = new FakeController()
    const audio = new FakeController()
    let fallback: Parameters<ListeningControllerFactory>[2] | null = null
    const implementation: ListeningControllerFactory = async (_item, _host, onFallback) => {
      fallback = onFallback
      return { controller: youtube, kind: 'youtube', message: null }
    }
    const factory = vi.fn(implementation)
    render(<ListeningWorkspace item={libraryItem} onBack={vi.fn()} controllerFactory={factory} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())

    act(() => fallback?.({ controller: audio, kind: 'audio', message: 'Video unavailable. Using saved audio.' }))

    expect(await screen.findByText('Video unavailable. Using saved audio.')).toBeInTheDocument()
    expect(youtube.destroy).toHaveBeenCalledOnce()
    expect(audio.seek).toHaveBeenCalledWith(libraryItem.listening_progress.last_position_seconds)
  })

  it('serializes bounded progress writes instead of patching on every clock tick', async () => {
    vi.useFakeTimers()
    const { fake } = setup()
    await act(async () => { await Promise.resolve() })
    if (fake) fake.time = 1.4
    act(() => vi.advanceTimersByTime(1000))
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/listening-progress'))).toHaveLength(0)
    if (fake) fake.time = 2.9
    act(() => vi.advanceTimersByTime(1000))
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/listening-progress'))).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await act(async () => { await Promise.resolve() })
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/listening-progress')).length).toBeLessThanOrEqual(1)
  })
})
