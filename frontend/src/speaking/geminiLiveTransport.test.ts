import { describe, expect, it, vi } from 'vitest'

import { speakingContext } from '../testFixtures/speaking'
import {
  BrowserGeminiTransport,
  decodeGeminiServerMessage,
  GeminiProviderError,
  type BrowserGeminiDependencies,
} from './geminiLiveProvider'

class FakeSocket {
  readonly sent: string[] = []
  readyState: number = WebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  send(data: string) { this.sent.push(data) }
  open() { this.readyState = WebSocket.OPEN; this.onopen?.(new Event('open')) }
  message(data: unknown) { this.onmessage?.({ data } as MessageEvent) }
  serverClose(code: number, reason = '') {
    this.readyState = WebSocket.CLOSED
    this.onclose?.({ code, reason } as CloseEvent)
  }
  close() { this.readyState = WebSocket.CLOSED }
}

function encoded(value: unknown): ArrayBuffer {
  return new TextEncoder().encode(JSON.stringify(value)).buffer
}

function makeAudioContext() {
  const outputNode = { connect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null as (() => void) | null, buffer: null }
  const processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null }
  const source = { connect: vi.fn(), disconnect: vi.fn() }
  const context = {
    sampleRate: 48_000,
    currentTime: 0,
    destination: {},
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
    createMediaStreamSource: vi.fn(() => source),
    createScriptProcessor: vi.fn(() => processor),
    createBuffer: vi.fn((_channels: number, length: number, rate: number) => ({
      duration: length / rate,
      getChannelData: () => new Float32Array(length),
    })),
    createBufferSource: vi.fn(() => outputNode),
  }
  return { context: context as unknown as AudioContext, outputNode, processor, source }
}

function createHarness(stream: MediaStream | null = null) {
  const socket = new FakeSocket()
  const audio = makeAudioContext()
  let timeoutCallback: (() => void) | null = null
  const dependencies: BrowserGeminiDependencies = {
    createWebSocket: vi.fn(() => socket as unknown as WebSocket),
    createAudioContext: vi.fn(() => audio.context),
    setTimeout: vi.fn((callback) => { timeoutCallback = callback; return 17 as unknown as ReturnType<typeof setTimeout> }),
    clearTimeout: vi.fn(),
  }
  const callbacks = {
    status: vi.fn(), transcript: vi.fn(), tool: vi.fn(), interrupted: vi.fn(), error: vi.fn(), ended: vi.fn(),
  }
  const transport = new BrowserGeminiTransport(stream, dependencies)
  const connect = () => transport.connect('ephemeral-token', 'gemini-3.8-live', {
    context: speakingContext,
    durationSeconds: 300,
  }, callbacks)
  return { transport, socket, audio, dependencies, callbacks, connect, timeout: () => timeoutCallback?.() }
}

async function completeSetup(harness: ReturnType<typeof createHarness>, message: unknown = new Blob([JSON.stringify({ setupComplete: {} })])) {
  const pending = harness.connect()
  harness.socket.open()
  harness.socket.message(message)
  await pending
}

describe('Gemini Live browser transport', () => {
  it('decodes string, Blob, and ArrayBuffer messages before parsing', async () => {
    const json = JSON.stringify({ setupComplete: {} })
    await expect(decodeGeminiServerMessage(json)).resolves.toBe(json)
    await expect(decodeGeminiServerMessage(new Blob([json]))).resolves.toBe(json)
    await expect(decodeGeminiServerMessage(new TextEncoder().encode(json).buffer)).resolves.toBe(json)
  })

  it('rejects unsupported message payloads with a controlled provider error', async () => {
    await expect(decodeGeminiServerMessage(new Uint8Array([1]))).rejects.toMatchObject({ code: 'message' })
  })

  it('waits for Blob setupComplete instead of treating socket open as ready', async () => {
    const harness = createHarness()
    let connected = false
    const pending = harness.connect().then(() => { connected = true })
    harness.socket.open()
    await Promise.resolve()
    expect(connected).toBe(false)
    expect(harness.callbacks.status).not.toHaveBeenCalledWith('listening')
    expect(() => harness.transport.sendText('too soon')).toThrowError(GeminiProviderError)

    harness.socket.message(new Blob([JSON.stringify({ setupComplete: {} })]))
    await pending
    expect(harness.callbacks.status).toHaveBeenCalledWith('listening')
  })

  it('accepts ArrayBuffer setupComplete and enables text only after readiness', async () => {
    const harness = createHarness()
    await completeSetup(harness, encoded({ setupComplete: {} }))
    harness.transport.sendText('Hallo')
    expect(JSON.parse(harness.socket.sent.at(-1) ?? '{}')).toEqual({
      clientContent: { turns: [{ role: 'user', parts: [{ text: 'Hallo' }] }], turnComplete: true },
    })
  })

  it('processes string server messages through the same normalized path', async () => {
    const harness = createHarness()
    const pending = harness.connect()
    harness.socket.open()
    harness.socket.message(JSON.stringify({ setupComplete: {} }))
    await pending
    harness.socket.message(JSON.stringify({ serverContent: { inputTranscription: { text: 'hello' } } }))
    await vi.waitFor(() => expect(harness.callbacks.transcript).toHaveBeenCalledWith({ role: 'learner', text: 'hello', final: false }))
  })

  it('fails safely when setupComplete times out', async () => {
    const harness = createHarness()
    const pending = harness.connect()
    harness.socket.open()
    harness.timeout()
    await expect(pending).rejects.toMatchObject({ code: 'setup' })
    expect(harness.socket.readyState).toBe(WebSocket.CLOSED)
  })

  it('treats close before setup as failure and sanitizes bounded diagnostics', async () => {
    const harness = createHarness()
    const pending = harness.connect()
    harness.socket.open()
    harness.socket.serverClose(4003, 'access_token=secret auth_tokens/another-secret')
    await expect(pending).rejects.toMatchObject({
      code: 'setup', closeCode: 4003, closeReason: 'access_token=[redacted] auth_tokens/[redacted]',
    })
  })

  it('treats normal close after setup as provider-ended behavior', async () => {
    const harness = createHarness()
    await completeSetup(harness)
    harness.socket.serverClose(1000, 'normal')
    expect(harness.callbacks.ended).toHaveBeenCalledOnce()
    expect(harness.callbacks.error).not.toHaveBeenCalled()
  })

  it('decodes Blob and ArrayBuffer serverContent transcripts and finalizes them in order', async () => {
    const harness = createHarness()
    await completeSetup(harness)
    harness.socket.message(new Blob([JSON.stringify({ serverContent: { inputTranscription: { text: 'Ich ' } } })]))
    harness.socket.message(encoded({ serverContent: { inputTranscription: { text: 'lerne' }, outputTranscription: { text: 'Gut' }, turnComplete: true } }))
    await vi.waitFor(() => expect(harness.callbacks.transcript).toHaveBeenCalledTimes(5))
    expect(harness.callbacks.transcript.mock.calls.map(([event]) => [event.role, event.text, event.final])).toEqual([
      ['learner', 'Ich ', false],
      ['learner', 'Ich lerne', false],
      ['agent', 'Gut', false],
      ['learner', 'Ich lerne', true],
      ['agent', 'Gut', true],
    ])
  })

  it('serializes asynchronous Blob decoding to preserve arrival order', async () => {
    const harness = createHarness()
    await completeSetup(harness)
    let releaseFirst!: (value: string) => void
    const delayed = new Blob([])
    Object.defineProperty(delayed, 'text', { value: () => new Promise<string>((resolve) => { releaseFirst = resolve }) })
    harness.socket.message(delayed)
    harness.socket.message(new Blob([JSON.stringify({ serverContent: { inputTranscription: { text: 'second' } } })]))
    await Promise.resolve()
    expect(harness.callbacks.transcript).not.toHaveBeenCalled()
    releaseFirst(JSON.stringify({ serverContent: { inputTranscription: { text: 'first ' } } }))
    await vi.waitFor(() => expect(harness.callbacks.transcript).toHaveBeenCalledTimes(2))
    expect(harness.callbacks.transcript.mock.calls.map(([event]) => event.text)).toEqual(['first ', 'first second'])
  })

  it('reports malformed Blob JSON and unsupported socket payloads as controlled errors', async () => {
    const harness = createHarness()
    await completeSetup(harness)
    harness.socket.message(new Blob(['not-json']))
    harness.socket.message(new Uint8Array([1, 2, 3]))
    await vi.waitFor(() => expect(harness.callbacks.error).toHaveBeenCalledTimes(2))
    expect(harness.callbacks.error.mock.calls.every(([error]) => error.code === 'message')).toBe(true)
  })

  it('dispatches binary audio, tool calls, interruption, and goAway without duplicating end', async () => {
    const harness = createHarness()
    await completeSetup(harness)
    harness.socket.message(new Blob([JSON.stringify({
      toolCall: { functionCalls: [{ id: 'one', name: 'get_due_chunks', args: { language: 'de' } }] },
      serverContent: { modelTurn: { parts: [{ inlineData: { data: 'AAA=' } }] } },
    })]))
    await vi.waitFor(() => expect(harness.audio.outputNode.start).toHaveBeenCalledOnce())
    expect(harness.callbacks.tool).toHaveBeenCalledWith({ id: 'one', name: 'get_due_chunks', arguments: { language: 'de' } })

    harness.socket.message(encoded({ serverContent: { interrupted: true } }))
    await vi.waitFor(() => expect(harness.callbacks.interrupted).toHaveBeenCalledOnce())
    expect(harness.audio.outputNode.stop).toHaveBeenCalledOnce()

    harness.socket.message(encoded({ goAway: { timeLeft: '1s' } }))
    await vi.waitFor(() => expect(harness.callbacks.ended).toHaveBeenCalledOnce())
    harness.socket.serverClose(1000, 'normal')
    expect(harness.callbacks.ended).toHaveBeenCalledOnce()
  })

  it('does not start microphone processing until setupComplete', async () => {
    const stream = {} as MediaStream
    const harness = createHarness(stream)
    const pending = harness.connect()
    harness.socket.open()
    expect(harness.audio.context.createMediaStreamSource).not.toHaveBeenCalled()
    harness.socket.message(new Blob([JSON.stringify({ setupComplete: {} })]))
    await pending
    expect(harness.audio.context.createMediaStreamSource).toHaveBeenCalledWith(stream)
  })
})
