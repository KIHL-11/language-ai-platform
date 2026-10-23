import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { dictionaryEntry } from '../testFixtures/dictionary'
import DictionaryPanel from './DictionaryPanel'


const fetchMock = vi.fn()
const play = vi.fn(async () => undefined)
const pause = vi.fn()

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

describe('DictionaryPanel', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    play.mockClear()
    pause.mockClear()
    fetchMock.mockResolvedValue(response(dictionaryEntry))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('Audio', FakeAudio)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows level one by default and leaves levels two and three collapsed', async () => {
    render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} onClose={vi.fn()} />)
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
    render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} onClose={onClose} />)
    await screen.findByText('A group of words that expresses a complete thought.')
    await user.click(screen.getByText('02 English → Deutsch'))
    await user.click(screen.getByText('03 English → 中文'))
    expect(screen.getByText('Satz')).toBeVisible()
    expect(screen.getByText('句子')).toBeVisible()
    expect(screen.getByText('Sentence 2.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Wiktionary data/ })).toHaveAttribute('href', 'https://en.wiktionary.org/wiki/sentence')
    await user.click(screen.getByRole('button', { name: 'Close dictionary' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('plays dictionary recordings and requests TTS only for an explicit fallback click', async () => {
    const user = userEvent.setup()
    fetchMock.mockImplementation((url: string) => String(url).endsWith('/api/tts')
      ? Promise.resolve(response({ audio_b64: 'YWJj', mime: 'audio/mpeg', marks: [] }))
      : Promise.resolve(response(dictionaryEntry)))
    render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} onClose={vi.fn()} />)
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
    const { rerender } = render(<DictionaryPanel selection={{ word: 'Sentence', sentence: 'Sentence 2.', language: 'en' }} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /US.*dictionary recording/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Pronunciation audio is unavailable')

    fetchMock.mockResolvedValueOnce(response({ ...dictionaryEntry, status: 'not_found', lemma: null }))
    rerender(<DictionaryPanel selection={{ word: 'Unknown', sentence: 'Unknown.', language: 'en' }} onClose={vi.fn()} />)
    expect(await screen.findByText(/No dictionary entry was found/)).toBeInTheDocument()

    fetchMock.mockResolvedValueOnce(response({ ...dictionaryEntry, status: 'not_configured', lemma: null }))
    rerender(<DictionaryPanel selection={{ word: 'Another', sentence: 'Another.', language: 'en' }} onClose={vi.fn()} />)
    expect(await screen.findByText(/Local dictionary data is not installed/)).toBeInTheDocument()

    fetchMock.mockRejectedValueOnce(new Error('offline'))
    rerender(<DictionaryPanel selection={{ word: 'Broken', sentence: 'Broken.', language: 'en' }} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Dictionary lookup failed'))
  })
})
