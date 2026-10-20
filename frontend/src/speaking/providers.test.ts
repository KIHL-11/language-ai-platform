import { afterEach, describe, expect, it, vi } from 'vitest'

import type { VoiceDialogueContext, VoiceEndReason, VoiceLiveDialogueProvider, VoiceDialogueStatus, VoiceTranscriptEvent, Unsubscribe } from '../training/voiceLiveDialogueProvider'
import { speakingContext } from '../testFixtures/speaking'
import { SpeakingApiError } from '../api/speaking'
import { GeminiLiveProvider, geminiLiveWebSocketUrl, type GeminiTransport } from './geminiLiveProvider'
import { OpenAISpeakingProvider } from './openAISpeakingProvider'

class FakeGeminiTransport implements GeminiTransport {
  callbacks: Parameters<GeminiTransport['connect']>[3] | null = null
  connect = vi.fn(async (_token, _model, _context, callbacks) => { this.callbacks = callbacks })
  mute = vi.fn(); sendText = vi.fn(); interrupt = vi.fn(); respondToTool = vi.fn(); close = vi.fn()
}

class FakeVoiceProvider implements VoiceLiveDialogueProvider {
  callbacks = { transcript: new Set<(event: VoiceTranscriptEvent) => void>(), status: new Set<(value: VoiceDialogueStatus) => void>(), interrupted: new Set<() => void>(), error: new Set<(error: Error) => void>(), ended: new Set<(reason: VoiceEndReason) => void>() }
  connect = vi.fn(async (_context: VoiceDialogueContext) => undefined); disconnect = vi.fn(async () => undefined); mute = vi.fn()
  onTranscript(listener: (event: VoiceTranscriptEvent) => void) { this.callbacks.transcript.add(listener); return () => this.callbacks.transcript.delete(listener) }
  onStatusChange(listener: (value: VoiceDialogueStatus) => void) { this.callbacks.status.add(listener); return () => this.callbacks.status.delete(listener) }
  onInterrupted(listener: () => void) { this.callbacks.interrupted.add(listener); return () => this.callbacks.interrupted.delete(listener) }
  onNotice(_listener: (message: string) => void): Unsubscribe { return () => undefined }
  onError(listener: (error: Error) => void) { this.callbacks.error.add(listener); return () => this.callbacks.error.delete(listener) }
  onEnded(listener: (reason: VoiceEndReason) => void) { this.callbacks.ended.add(listener); return () => this.callbacks.ended.delete(listener) }
}

describe('Speaking provider contract', () => {
  afterEach(() => vi.restoreAllMocks())

  it('uses the official v1beta constrained Live endpoint for ephemeral tokens', () => {
    const url = geminiLiveWebSocketUrl('auth_tokens/token value')
    expect(url).toBe('wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained?access_token=auth_tokens%2Ftoken%20value')
    expect(url).not.toContain('v1alpha')
  })

  it('Gemini normalizes lifecycle, transcripts, interruption, tools, mute, text, and cleanup', async () => {
    const transport = new FakeGeminiTransport()
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const provider = new GeminiLiveProvider({
      fetchToken: vi.fn(async () => ({ value: 'ephemeral', model: 'gemini-3.8-live', expires_at: 'soon' })),
      getUserMedia,
      createTransport: () => transport,
    })
    const transcripts = vi.fn(), tools = vi.fn(), interrupted = vi.fn()
    provider.onTranscript(transcripts); provider.onToolCall(tools); provider.onInterrupted(interrupted)
    await provider.connect({ context: speakingContext, durationSeconds: 300 })
    expect(getUserMedia).toHaveBeenCalledOnce()
    transport.callbacks?.transcript({ role: 'learner', text: 'partial', final: false })
    transport.callbacks?.transcript({ role: 'learner', text: 'final', final: true })
    transport.callbacks?.tool({ id: 'tool-1', name: 'get_due_chunks', arguments: {} })
    transport.callbacks?.interrupted()
    provider.mute(true); await provider.sendText?.('hello'); provider.respondToTool?.('tool-1', { ok: true })
    expect(transcripts).toHaveBeenCalledTimes(2); expect(tools).toHaveBeenCalledOnce(); expect(interrupted).toHaveBeenCalledOnce()
    expect(transport.mute).toHaveBeenCalledWith(true); expect(transport.sendText).toHaveBeenCalledWith('hello')
    await provider.disconnect('user')
    expect(transport.close).toHaveBeenCalledOnce(); expect(stop).toHaveBeenCalledOnce()
  })

  it('Gemini releases microphone and never creates another provider when connect fails', async () => {
    const stop = vi.fn()
    const provider = new GeminiLiveProvider({
      fetchToken: vi.fn(async () => ({ value: 'ephemeral', model: 'gemini-3.8-live', expires_at: 'soon' })),
      getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream)),
      createTransport: () => ({ ...new FakeGeminiTransport(), connect: vi.fn(async () => { throw new Error('quota') }) }),
    })
    await expect(provider.connect({ context: speakingContext, durationSeconds: 300 })).rejects.toMatchObject({ code: 'websocket' })
    expect(stop).toHaveBeenCalledOnce()
  })

  it.each([
    [new SpeakingApiError('failed', 502), 'credential'],
    [new SpeakingApiError('limited', 429), 'quota'],
  ])('classifies Gemini credential failures without requesting a microphone', async (failure, code) => {
    const getUserMedia = vi.fn()
    const provider = new GeminiLiveProvider({ fetchToken: vi.fn(async () => { throw failure }), getUserMedia })
    await expect(provider.connect({ context: speakingContext, durationSeconds: 300 })).rejects.toMatchObject({ code })
    expect(getUserMedia).not.toHaveBeenCalled()
  })

  it.each([
    ['NotAllowedError', 'microphone_permission'],
    ['NotFoundError', 'microphone_unavailable'],
  ])('classifies microphone failure %s before opening the socket', async (name, code) => {
    const createTransport = vi.fn()
    const provider = new GeminiLiveProvider({
      fetchToken: vi.fn(async () => ({ value: 'ephemeral', model: 'gemini-3.8-live', expires_at: 'soon' })),
      getUserMedia: vi.fn(async () => { throw Object.assign(new Error('device'), { name }) }),
      createTransport,
    })
    await expect(provider.connect({ context: speakingContext, durationSeconds: 300 })).rejects.toMatchObject({ code })
    expect(createTransport).not.toHaveBeenCalled()
  })

  it('Gemini text fallback connects without requesting a microphone', async () => {
    const transport = new FakeGeminiTransport()
    const getUserMedia = vi.fn()
    const createTransport = vi.fn(() => transport)
    const provider = new GeminiLiveProvider({
      fetchToken: vi.fn(async () => ({ value: 'ephemeral', model: 'gemini-3.8-live', expires_at: 'soon' })),
      getUserMedia,
      createTransport,
    })
    await provider.connect({ context: speakingContext, durationSeconds: 300, textOnly: true })
    expect(getUserMedia).not.toHaveBeenCalled()
    expect(createTransport).toHaveBeenCalledWith(null)
    await provider.disconnect()
  })

  it('OpenAI adapter satisfies normalized final transcript and cleanup behavior', async () => {
    const voice = new FakeVoiceProvider()
    const provider = new OpenAISpeakingProvider(() => voice)
    const transcript = vi.fn(), interrupted = vi.fn()
    provider.onTranscript(transcript); provider.onInterrupted(interrupted)
    await provider.connect({ context: speakingContext, durationSeconds: 600 })
    voice.callbacks.transcript.forEach((listener) => listener({ id: 'one', role: 'learner', text: 'hello' }))
    voice.callbacks.interrupted.forEach((listener) => listener())
    expect(transcript).toHaveBeenCalledWith(expect.objectContaining({ role: 'learner', final: true }))
    expect(interrupted).toHaveBeenCalledOnce()
    provider.mute(true); expect(voice.mute).toHaveBeenCalledWith(true)
    await provider.disconnect('user'); expect(voice.disconnect).toHaveBeenCalled()
  })
})
