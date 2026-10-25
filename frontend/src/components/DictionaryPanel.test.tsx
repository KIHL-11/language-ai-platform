import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { dictionaryEntry } from '../testFixtures/dictionary'
import { studyItem } from '../testFixtures/studyBank'
import { calculateDictionaryPopoverPosition } from '../dictionary/popoverPosition'
import DictionaryPanel from './DictionaryPanel'


const fetchMock = vi.fn()
const play = vi.fn(async () => undefined)
const pause = vi.fn()
const anchors: HTMLElement[] = []

class FakeAudio {
  src: string
  constructor(src: string) { this.src = src }
  private ended: (() => void) | null = null
  play = async () => {
    await play()
    this.ended?.()
  }
  pause = pause
  addEventListener = vi.fn((event: string, listener: () => void) => {
    if (event === 'ended') this.ended = listener
  })
}

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function createAnchor(rect: Partial<DOMRect> = {}) {
  const anchor = document.createElement('button')
  anchor.textContent = 'word'
  document.body.appendChild(anchor)
  anchors.push(anchor)
  vi.spyOn(anchor, 'getBoundingClientRect').mockReturnValue({
    top: 100, right: 160, bottom: 124, left: 100, width: 60, height: 24,
    x: 100, y: 100, toJSON: () => ({}), ...rect,
  })
  return anchor
}

function panel(anchor = createAnchor(), word = 'Sentence') {
  return <DictionaryPanel
    selection={{ word, sentence: `${word} 2.`, language: 'en' }}
    anchor={anchor}
    onClose={vi.fn()}
  />
}

function contextualPanel(
  anchor = createAnchor(),
  updates: Partial<{ word: string; sentence: string; sentenceId: number }> = {},
  onStudyItemSaved = vi.fn(),
) {
  const word = updates.word ?? 'Sentence'
  return <DictionaryPanel
    selection={{
      word,
      sentence: updates.sentence ?? `${word} 2.`,
      language: 'en',
      libraryItemId: 'library-item',
      sentenceId: updates.sentenceId ?? 2,
      learnerId: studyItem.user_id,
    }}
    anchor={anchor}
    onClose={vi.fn()}
    onStudyItemSaved={onStudyItemSaved}
  />
}

describe('DictionaryPanel', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    play.mockClear()
    pause.mockClear()
    fetchMock.mockResolvedValue(response(dictionaryEntry))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('Audio', FakeAudio)
  })

  it('renders the anchored shell and loading state before lookup completes', () => {
    fetchMock.mockReturnValue(new Promise(() => undefined))

    render(panel())

    expect(screen.getByLabelText('Contextual dictionary')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sentence' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Looking up Sentence…')
  })
  afterEach(() => {
    anchors.splice(0).forEach((anchor) => anchor.remove())
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('anchors within viewport, flips above near the bottom, and uses a narrow bottom sheet', () => {
    const rightEdge = calculateDictionaryPopoverPosition(
      { top: 100, right: 810, bottom: 124, left: 780 }, 300, 820, 700,
    )
    expect(rightEdge.placement).toBe('below')
    expect(rightEdge.style.left).toBe(468)
    expect(rightEdge.style.width).toBe(340)

    const bottomEdge = calculateDictionaryPopoverPosition(
      { top: 650, right: 160, bottom: 674, left: 100 }, 300, 900, 700,
    )
    expect(bottomEdge.placement).toBe('above')
    expect(bottomEdge.style.top).toBe(342)

    const narrow = calculateDictionaryPopoverPosition(
      { top: 100, right: 160, bottom: 124, left: 100 }, 300, 390, 700,
    )
    expect(narrow.placement).toBe('sheet')
    expect(narrow.style).toMatchObject({ left: 15, right: 15, bottom: 12 })
  })

  it('shows level one by default and leaves levels two and three collapsed', async () => {
    render(panel())
    expect(await screen.findByRole('heading', { name: 'sentence' })).toBeInTheDocument()
    const level1 = screen.getByText('01 English → English').closest('details')
    const level2 = screen.getByText('02 English → Deutsch').closest('details')
    const level3 = screen.getByText('03 English → 中文').closest('details')
    expect(level1).toHaveAttribute('open')
    expect(level2).not.toHaveAttribute('open')
    expect(level3).not.toHaveAttribute('open')
  })

  it('allows independent disclosure, shows context and attribution, and closes', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={createAnchor()} onClose={onClose} />)
    await screen.findByText('A group of words that expresses a complete thought.')
    await user.click(screen.getByText('02 English → Deutsch'))
    await user.click(screen.getByText('03 English → 中文'))
    expect(screen.getByText('Satz')).toBeVisible()
    expect(screen.getByText('句子')).toBeVisible()
    await user.click(screen.getByText('More'))
    expect(screen.getByText('Sentence 2.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Wiktionary data/ })).toHaveAttribute('href', 'https://en.wiktionary.org/wiki/sentence')
    await user.click(screen.getByRole('button', { name: 'Close dictionary' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('loads optional inflections only when More is expanded', async () => {
    const user = userEvent.setup()
    let resolveDetails: ((value: ReturnType<typeof response>) => void) | undefined
    fetchMock
      .mockResolvedValueOnce(response({ ...dictionaryEntry, inflections: [] }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveDetails = resolve }))
    render(panel())
    await screen.findByText('A group of words that expresses a complete thought.')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      include_inflections: false,
    })
    await user.click(screen.getByText('More'))

    expect(screen.getByRole('status')).toHaveTextContent('Loading inflections…')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      include_inflections: true,
    })
    resolveDetails?.(response(dictionaryEntry))
    expect(await screen.findByText('sentences')).toBeInTheDocument()
  })

  it('keeps dictionary details usable when the forms sidecar is unavailable', async () => {
    const user = userEvent.setup()
    fetchMock
      .mockResolvedValueOnce(response({ ...dictionaryEntry, inflections: [], inflections_status: 'not_requested' }))
      .mockResolvedValueOnce(response({
        ...dictionaryEntry,
        inflections: [],
        inflections_status: 'index_unavailable',
        inflections_message: 'Build the local dictionary forms index to load inflections.',
      }))

    render(panel())
    expect(await screen.findByText('A group of words that expresses a complete thought.')).toBeVisible()
    await user.click(screen.getByText('More'))

    expect(await screen.findByText('Build the local dictionary forms index to load inflections.')).toBeVisible()
    expect(screen.getByText('A judicial punishment.')).toBeVisible()
    expect(screen.getByRole('button', { name: /US.*dictionary recording/ })).toBeEnabled()
  })

  it('plays dictionary recordings and requests TTS only for an explicit fallback click', async () => {
    const user = userEvent.setup()
    fetchMock.mockImplementation((url: string) => String(url).endsWith('/api/tts')
      ? Promise.resolve(response({ audio_b64: 'YWJj', mime: 'audio/mpeg', marks: [] }))
      : Promise.resolve(response(dictionaryEntry)))
    render(panel())
    await user.click(await screen.findByRole('button', { name: /US.*dictionary recording/ }))
    expect(play).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/tts'))).toHaveLength(0)
    await waitFor(() => expect(screen.getByRole('button', { name: /en-GB.*TTS/ })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: /en-GB.*TTS/ }))
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/tts'))).toHaveLength(1)
    expect(play).toHaveBeenCalledTimes(2)
  })

  it('handles pronunciation, lookup, unknown, and unconfigured states without crashing', async () => {
    const user = userEvent.setup()
    play.mockRejectedValueOnce(new Error('offline'))
    const anchor = createAnchor()
    const onClose = vi.fn()
    const { rerender } = render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    await user.click(await screen.findByRole('button', { name: /US.*dictionary recording/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Pronunciation audio is unavailable')

    fetchMock.mockResolvedValueOnce(response({ ...dictionaryEntry, status: 'not_found', lemma: null }))
    rerender(<DictionaryPanel selection={{ word: 'Unknown', sentence: 'Unknown.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    expect(await screen.findByText(/No dictionary entry was found/)).toBeInTheDocument()

    fetchMock.mockResolvedValueOnce(response({ ...dictionaryEntry, status: 'not_configured', lemma: null }))
    rerender(<DictionaryPanel selection={{ word: 'Another', sentence: 'Another.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    expect(await screen.findByText(/Local dictionary data is not installed/)).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new Error('offline'))
    rerender(<DictionaryPanel selection={{ word: 'Broken', sentence: 'Broken.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Dictionary lookup failed'))
  })

  it('closes with Escape or an outside click and restores word focus', async () => {
    const user = userEvent.setup()
    const anchor = createAnchor()
    const onClose = vi.fn()
    const { rerender } = render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    await screen.findByRole('heading', { name: 'sentence' })
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
    expect(anchor).toHaveFocus()

    onClose.mockClear()
    rerender(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    await user.click(document.body)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('closes safely when scrolling moves context or the word anchor disappears', async () => {
    const anchor = createAnchor()
    const onClose = vi.fn()
    const { rerender } = render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    await screen.findByRole('heading', { name: 'sentence' })
    document.dispatchEvent(new Event('scroll'))
    expect(onClose).toHaveBeenCalledOnce()

    onClose.mockClear()
    rerender(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    anchor.remove()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('switches words in one popover without showing a stale response', async () => {
    let resolveFirst: ((value: ReturnType<typeof response>) => void) | undefined
    fetchMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(response({ ...dictionaryEntry, query: 'Bank', surface_form: 'Bank', lemma: 'bank' }))
    const anchor = createAnchor()
    const onClose = vi.fn()
    const { rerender } = render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    rerender(<DictionaryPanel selection={{ word: 'Bank', sentence: 'Bank 2.', language: 'en' }} anchor={anchor} onClose={onClose} />)
    expect(await screen.findByRole('heading', { name: 'bank' })).toBeInTheDocument()
    resolveFirst?.(response(dictionaryEntry))
    await Promise.resolve()
    expect(screen.getByRole('heading', { name: 'bank' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'sentence' })).not.toBeInTheDocument()
  })

  it('saves with authoritative identifiers, locks duplicate clicks, and reports success', async () => {
    const user = userEvent.setup()
    let resolveSave: ((value: ReturnType<typeof response>) => void) | undefined
    const onSaved = vi.fn()
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/dictionary/lookup')) return Promise.resolve(response(dictionaryEntry))
      if (String(url).includes('/status')) return Promise.resolve(response({ status: 'unsaved', item: null }))
      if (options?.method === 'POST' && String(url).includes('/study-bank/words')) {
        return new Promise((resolve) => { resolveSave = resolve })
      }
      return Promise.resolve(response({}))
    })
    render(contextualPanel(createAnchor(), {}, onSaved))
    await user.click(await screen.findByRole('button', { name: 'Save Word' }))
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Saving…' }))
    const saveCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/study-bank/words'))
    expect(saveCalls).toHaveLength(1)
    expect(JSON.parse(String(saveCalls[0][1]?.body))).toEqual({
      user_id: studyItem.user_id, sentence_id: 2, word: 'Sentence',
    })
    resolveSave?.(response({ item: { ...studyItem, text: 'Sentence', lemma: 'sentence', normalized_text: 'sentence' }, created: true }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(onSaved).toHaveBeenCalledOnce()
  })

  it('shows previously saved and archived states and restores only on explicit action', async () => {
    const user = userEvent.setup()
    const archived = { ...studyItem, active: false, text: 'Sentence', lemma: 'sentence', normalized_text: 'sentence' }
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/dictionary/lookup')) return Promise.resolve(response(dictionaryEntry))
      if (String(url).includes('/status')) return Promise.resolve(response({ status: 'archived', item: archived }))
      if (options?.method === 'PATCH') return Promise.resolve(response({ ...archived, active: true }))
      return Promise.resolve(response({}))
    })
    render(contextualPanel())
    expect(await screen.findByText('Previously saved · Archived')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Restore' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    const restore = fetchMock.mock.calls.find(([, options]) => options?.method === 'PATCH')
    expect(String(restore?.[0])).toContain(`/study-bank/items/${studyItem.id}`)
    expect(JSON.parse(String(restore?.[1]?.body))).toEqual({ active: true })
  })

  it('recognizes an active word without issuing another save', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/dictionary/lookup')) return Promise.resolve(response(dictionaryEntry))
      if (String(url).includes('/status')) return Promise.resolve(response({ status: 'saved', item: studyItem }))
      return Promise.resolve(response({}))
    })
    render(contextualPanel())
    expect(await screen.findByText('Previously Saved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Word' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url, options]) =>
      String(url).endsWith('/study-bank/words') && options?.method === 'POST')).toBe(false)
  })

  it('keeps the panel open after save failure and allows an explicit retry', async () => {
    const user = userEvent.setup()
    let attempts = 0
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/dictionary/lookup')) return Promise.resolve(response(dictionaryEntry))
      if (String(url).includes('/status')) return Promise.resolve(response({ status: 'unsaved', item: null }))
      if (options?.method === 'POST') {
        attempts += 1
        return attempts === 1
          ? Promise.resolve(response({}, 500))
          : Promise.resolve(response({ item: studyItem, created: true }))
      }
      return Promise.resolve(response({}))
    })
    render(contextualPanel())
    await user.click(await screen.findByRole('button', { name: 'Save Word' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Save Failed')
    expect(screen.getByLabelText('Contextual dictionary')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(attempts).toBe(2)
  })

  it('keys status by sentence id and ignores stale status after rapid switching', async () => {
    let resolveFirstStatus: ((value: ReturnType<typeof response>) => void) | undefined
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/dictionary/lookup')) return Promise.resolve(response(dictionaryEntry))
      if (String(url).includes('sentence_id=2')) {
        return new Promise((resolve) => { resolveFirstStatus = resolve })
      }
      if (String(url).includes('sentence_id=3')) return Promise.resolve(response({ status: 'unsaved', item: null }))
      return Promise.resolve(response({}))
    })
    const anchor = createAnchor()
    const { rerender } = render(contextualPanel(anchor, { sentence: 'Same.', sentenceId: 2 }))
    await screen.findByText('A group of words that expresses a complete thought.')
    rerender(contextualPanel(anchor, { sentence: 'Same.', sentenceId: 3 }))
    expect(await screen.findByRole('button', { name: 'Save Word' })).toBeInTheDocument()
    resolveFirstStatus?.(response({ status: 'saved', item: studyItem }))
    await Promise.resolve()
    expect(screen.getByRole('button', { name: 'Save Word' })).toBeInTheDocument()
    expect(screen.queryByText('Previously Saved')).not.toBeInTheDocument()
  })
})
