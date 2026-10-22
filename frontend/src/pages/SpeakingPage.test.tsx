import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { librarySummary } from '../testFixtures/libraryItem'
import { speakingContext, speakingProviders, speakingSession } from '../testFixtures/speaking'
import type { SpeakingAgentProvider, SpeakingConnectionStatus, SpeakingToolCall, SpeakingTranscriptEvent } from '../speaking/provider'
import { GeminiProviderError, type GeminiFailureCode } from '../speaking/geminiLiveProvider'
import type { SpeakingEndReason } from '../types/speaking'
import SpeakingPage from './SpeakingPage'

class FakeProvider implements SpeakingAgentProvider {
  readonly id = 'gemini-live' as const
  listeners = { status: new Set<(value: SpeakingConnectionStatus) => void>(), transcript: new Set<(value: SpeakingTranscriptEvent) => void>(), tool: new Set<(value: SpeakingToolCall) => void>(), interrupted: new Set<() => void>(), error: new Set<(value: Error) => void>(), ended: new Set<(value: SpeakingEndReason) => void>() }
  connect = vi.fn(async () => { this.emitStatus('listening') }); disconnect = vi.fn(async () => undefined); mute = vi.fn(); sendText = vi.fn(async () => undefined); interrupt = vi.fn(); respondToTool = vi.fn()
  onStatus = (listener: (value: SpeakingConnectionStatus) => void) => { this.listeners.status.add(listener); return () => this.listeners.status.delete(listener) }
  onTranscript = (listener: (value: SpeakingTranscriptEvent) => void) => { this.listeners.transcript.add(listener); return () => this.listeners.transcript.delete(listener) }
  onToolCall = (listener: (value: SpeakingToolCall) => void) => { this.listeners.tool.add(listener); return () => this.listeners.tool.delete(listener) }
  onInterrupted = (listener: () => void) => { this.listeners.interrupted.add(listener); return () => this.listeners.interrupted.delete(listener) }
  onError = (listener: (value: Error) => void) => { this.listeners.error.add(listener); return () => this.listeners.error.delete(listener) }
  onEnded = (listener: (value: SpeakingEndReason) => void) => { this.listeners.ended.add(listener); return () => this.listeners.ended.delete(listener) }
  emitStatus(value: SpeakingConnectionStatus) { this.listeners.status.forEach((listener) => listener(value)) }
  emitTranscript(value: SpeakingTranscriptEvent) { this.listeners.transcript.forEach((listener) => listener(value)) }
}

const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

function updatedSession(eventCount = 1) {
  return {
    ...speakingSession, event_count: eventCount, learner_turn_count: 1,
    turns: [{ sequence: 1, role: 'learner' as const, text: 'As far as I know, yes.', created_at: '2026-10-20T10:01:00Z', provider_turn_id: 'turn-1' }],
    target_usage: [{ ...speakingSession.target_usage[0], use_count: 1, first_used_turn: 1, last_used_turn: 1, evidence: ['As far as I know, yes.'] }],
  }
}

function routes(url: string, options?: RequestInit) {
  const value = String(url)
  if (value.endsWith('/api/speaking/providers')) return Promise.resolve(response({ providers: speakingProviders }))
  if (value.includes('/api/library/items')) return Promise.resolve(response({ items: [librarySummary] }))
  if (value.includes('/api/speaking/sessions?')) return Promise.resolve(response({ sessions: [] }))
  if (value.endsWith('/api/speaking/context')) return Promise.resolve(response(speakingContext))
  if (value.endsWith('/api/speaking/sessions') && options?.method === 'POST') return Promise.resolve(response({ session: speakingSession, context: speakingContext }))
  if (value.includes('/events')) return Promise.resolve(response(updatedSession()))
  if (value.includes('/finish')) return Promise.resolve(response({ ...updatedSession(), status: 'completed', ended_at: '2026-10-20T10:05:00Z', event_count: 2, end_reason: 'user' }))
  if (value.includes('/tools')) return Promise.resolve(response({ result: [], session: null }))
  if (value.includes('/api/integration/target-languages')) return Promise.resolve(response({ target_languages: ['zh-CN'] }))
  if (value.includes('/api/integration/speaking/') && options?.method === 'POST') return Promise.resolve(response({ item: {}, created: true, chunk_memory_synchronized: true }))
  return Promise.resolve(response({}))
}

describe('Speaking Page', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    localStorage.clear(); fetchMock.mockReset(); fetchMock.mockImplementation(routes); vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('loads setup, defaults to Gemini and five minutes, and never connects on open', async () => {
    const factory = vi.fn(() => new FakeProvider())
    render(<SpeakingPage providerFactory={factory} />)
    expect(await screen.findByRole('heading', { name: 'Speaking' })).toBeInTheDocument()
    expect(screen.getByLabelText('Provider')).toHaveValue('gemini-live')
    expect(screen.getByLabelText('Session length')).toHaveValue('300')
    expect(await screen.findByText('as far as I know')).toBeInTheDocument()
    expect(factory).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('client-token'))).toBe(false)
  })

  it('supports modes, language, level, media, target deselection, and explicit start ordering', async () => {
    const user = userEvent.setup()
    const provider = new FakeProvider(); const factory = vi.fn(() => provider)
    render(<SpeakingPage providerFactory={factory} />)
    await screen.findByText('as far as I know')
    await user.selectOptions(screen.getByLabelText('Level'), 'C1')
    await user.selectOptions(screen.getByLabelText('Practice mode'), 'media')
    await user.selectOptions(screen.getByLabelText('Saved media'), librarySummary.id)
    await user.click(screen.getByRole('checkbox', { name: /as far as I know/i }))
    await user.click(screen.getByRole('button', { name: 'Start Speaking' }))
    await waitFor(() => expect(provider.connect).toHaveBeenCalledOnce())
    const calls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(calls.findIndex((url) => url.endsWith('/api/speaking/sessions'))).toBeLessThan(calls.length)
    expect(screen.getByText(/Gemini Live · EN C1/)).toBeInTheDocument()
  })

  it('persists only final learner transcript, displays evidence, supports mute/text, and reviews on end', async () => {
    const user = userEvent.setup(); const provider = new FakeProvider()
    render(<SpeakingPage providerFactory={() => provider} />)
    await screen.findByText('as far as I know'); await user.click(screen.getByRole('button', { name: 'Start Speaking' }))
    await waitFor(() => expect(provider.connect).toHaveBeenCalled())
    act(() => provider.emitTranscript({ role: 'learner', text: 'partial words', final: false }))
    expect(screen.getByText(/partial words/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/events'))).toHaveLength(0)
    act(() => provider.emitTranscript({ role: 'learner', text: 'As far as I know, yes.', final: true, providerTurnId: 'turn-1' }))
    expect(await screen.findByText(/✓ Used/)).toBeInTheDocument()
    expect(screen.getByText(/“As far as I know, yes.”/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mute' })); expect(provider.mute).toHaveBeenCalledWith(true)
    await user.click(screen.getByRole('button', { name: 'Switch to Text Input' })); await user.type(screen.getByLabelText('Text message'), 'typed fallback'); await user.click(screen.getByRole('button', { name: 'Send' })); expect(provider.sendText).toHaveBeenCalledWith('typed fallback')
    await user.click(screen.getByRole('button', { name: 'End Session' }))
    expect(await screen.findByRole('heading', { name: 'Session complete' })).toBeInTheDocument()
    expect(screen.getByText(/Learner turns: 1/)).toBeInTheDocument()
    expect(screen.queryByText(/fluency score/i)).not.toBeInTheDocument()
  })

  it('shows correction/suggestion review and recent detail without raw audio', async () => {
    const completed = { ...updatedSession(), status: 'completed' as const, ended_at: '2026-10-20T10:05:00Z', end_reason: 'user' as const, corrections: [{ learner_text: 'I goes', corrected_text: 'I go', short_explanation: 'Agreement', category: 'grammar' as const }], suggestions: [{ text: 'that makes sense', meaning: '合理', kind: 'chunk' as const, short_usage_note: 'reaction', originating_turn: 1 }] }
    fetchMock.mockImplementation((url: string, options?: RequestInit) => String(url).includes('/api/speaking/sessions?') ? Promise.resolve(response({ sessions: [completed] })) : routes(url, options))
    const user = userEvent.setup(); render(<SpeakingPage providerFactory={() => new FakeProvider()} />)
    await user.click(await screen.findByRole('button', { name: /Review weak expressions/ }))
    expect(screen.getByText('I goes → I go: Agreement')).toBeInTheDocument()
    expect(screen.getByText('that makes sense — 合理')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('audio_b64')
  })

  it('requires an explicit target-language choice and saves a canonical session suggestion', async () => {
    const completed = { ...updatedSession(), status: 'completed' as const, ended_at: '2026-10-20T10:05:00Z', end_reason: 'user' as const, suggestions: [{ text: 'that makes sense', meaning: '合理', kind: 'chunk' as const, short_usage_note: 'reaction', originating_turn: 1 }] }
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (String(url).includes('/api/speaking/sessions?')) return Promise.resolve(response({ sessions: [completed] }))
      if (String(url).includes('/api/integration/target-languages')) return Promise.resolve(response({ target_languages: ['zh-CN', 'fr'] }))
      return routes(url, options)
    })
    const user = userEvent.setup(); render(<SpeakingPage providerFactory={() => new FakeProvider()} />)
    await user.click(await screen.findByRole('button', { name: /Review weak expressions/ }))
    const save = await screen.findByRole('button', { name: 'Save to Words & Chunks' })
    expect(save).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Target language for that makes sense'), 'fr')
    await user.click(save)
    expect(await screen.findByText('Saved to Words & Chunks')).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([url], index) => String(url).includes('/api/integration/speaking/') && fetchMock.mock.calls[index][1]?.method === 'POST')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ user_id: expect.any(String), target_language: 'fr' })
  })

  it('does not silently instantiate OpenAI when Gemini fails', async () => {
    const gemini = new FakeProvider(); gemini.connect.mockRejectedValue(new GeminiProviderError('quota', 'limited'))
    const factory = vi.fn(() => gemini)
    const user = userEvent.setup(); render(<SpeakingPage providerFactory={factory} />)
    await screen.findByText('as far as I know'); await user.click(screen.getByRole('button', { name: 'Start Speaking' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('quota or rate limit')
    expect(factory).toHaveBeenCalledTimes(1); expect(factory).toHaveBeenCalledWith('gemini-live')
  })

  it.each([
    ['credential', 'Gemini credential request failed'],
    ['websocket', 'Gemini Live WebSocket connection failed'],
    ['setup', 'Gemini Live setup did not complete'],
    ['message', 'Gemini Live returned an unreadable server message'],
    ['microphone_permission', 'Microphone permission denied'],
  ] satisfies Array<[GeminiFailureCode, string]>)('shows actionable %s diagnostics without credentials', async (code, message) => {
    const provider = new FakeProvider()
    provider.connect.mockRejectedValue(new GeminiProviderError(code, 'internal detail'))
    const user = userEvent.setup(); render(<SpeakingPage providerFactory={() => provider} />)
    await screen.findByText('as far as I know'); await user.click(screen.getByRole('button', { name: 'Start Speaking' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(message)
    expect(alert).not.toHaveTextContent('internal detail')
    expect(alert).not.toHaveTextContent('auth_tokens/')
  })

  it('starts Gemini text fallback without voice mode and exposes the text input immediately', async () => {
    const provider = new FakeProvider(); const user = userEvent.setup()
    render(<SpeakingPage providerFactory={() => provider} />)
    await screen.findByText('as far as I know')
    await user.click(screen.getByRole('checkbox', { name: /Start with Text Input/i }))
    await user.click(screen.getByRole('button', { name: 'Start Speaking' }))
    await waitFor(() => expect(provider.connect).toHaveBeenCalledWith(expect.objectContaining({ textOnly: true })))
    expect(screen.getByLabelText('Text message')).toBeInTheDocument()
  })

  it('connects OpenAI only after explicit provider selection and start', async () => {
    const provider = new FakeProvider(); const factory = vi.fn(() => provider); const user = userEvent.setup()
    render(<SpeakingPage providerFactory={factory} />)
    await screen.findByText('as far as I know')
    await user.selectOptions(screen.getByLabelText('Provider'), 'openai-realtime')
    expect(factory).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Start Speaking' }))
    await waitFor(() => expect(factory).toHaveBeenCalledWith('openai-realtime'))
    expect(provider.connect).toHaveBeenCalledWith(expect.objectContaining({ textOnly: false }))
  })

  it('disconnects provider on unmount', async () => {
    const provider = new FakeProvider(); const user = userEvent.setup()
    const { unmount } = render(<SpeakingPage providerFactory={() => provider} />)
    await screen.findByText('as far as I know'); await user.click(screen.getByRole('button', { name: 'Start Speaking' })); await waitFor(() => expect(provider.connect).toHaveBeenCalled())
    unmount(); expect(provider.disconnect).toHaveBeenCalledWith('page_exit')
  })
})
