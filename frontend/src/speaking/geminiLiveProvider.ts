import { createGeminiToken, SpeakingApiError } from '../api/speaking'
import type { SpeakingEndReason } from '../types/speaking'
import { ProviderEvents, type SpeakingAgentProvider, type SpeakingConnectionStatus, type SpeakingProviderContext, type SpeakingToolCall, type SpeakingTranscriptEvent } from './provider'

interface GeminiTransportCallbacks {
  status: (status: 'listening' | 'agent_speaking' | 'learner_speaking') => void
  transcript: (event: SpeakingTranscriptEvent) => void
  tool: (event: SpeakingToolCall) => void
  interrupted: () => void
  error: (error: Error) => void
  ended: () => void
}

export interface GeminiTransport {
  connect(token: string, model: string, context: SpeakingProviderContext, callbacks: GeminiTransportCallbacks): Promise<void>
  mute(muted: boolean): void
  sendText(text: string): void
  interrupt(): void
  respondToTool(id: string, result: unknown): void
  close(): void
}

export interface GeminiDependencies {
  fetchToken: typeof createGeminiToken
  getUserMedia: () => Promise<MediaStream>
  createTransport: (stream: MediaStream | null) => GeminiTransport
}

export type GeminiFailureCode = 'credential' | 'quota' | 'microphone_permission' | 'microphone_unavailable' | 'websocket' | 'setup' | 'message'

export class GeminiProviderError extends Error {
  readonly code: GeminiFailureCode
  readonly closeCode?: number
  readonly closeReason?: string

  constructor(code: GeminiFailureCode, message: string, close?: { code: number; reason: string }) {
    super(message)
    this.code = code
    this.closeCode = close?.code
    this.closeReason = close?.reason
  }
}

export type GeminiServerMessage = string | Blob | ArrayBuffer

export async function decodeGeminiServerMessage(data: unknown): Promise<string> {
  if (typeof data === 'string') return data
  if (data instanceof Blob) return data.text()
  if (data instanceof ArrayBuffer || Object.prototype.toString.call(data) === '[object ArrayBuffer]') {
    return new TextDecoder().decode(new Uint8Array(data as ArrayBuffer))
  }
  throw new GeminiProviderError('message', 'Gemini Live returned an unsupported server message.')
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (const value of bytes) binary += String.fromCharCode(value)
  return btoa(binary)
}

function floatToPcm16(input: Float32Array, sourceRate: number): Uint8Array {
  const ratio = sourceRate / 16_000
  const result = new Int16Array(Math.max(1, Math.floor(input.length / ratio)))
  for (let index = 0; index < result.length; index += 1) {
    const sample = input[Math.min(input.length - 1, Math.floor(index * ratio))]
    result[index] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)))
  }
  return new Uint8Array(result.buffer)
}

export function geminiLiveWebSocketUrl(token: string): string {
  return `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained?access_token=${encodeURIComponent(token)}`
}

const SETUP_TIMEOUT_MS = 10_000

export interface BrowserGeminiDependencies {
  createWebSocket: (url: string) => WebSocket
  createAudioContext: () => AudioContext
  setTimeout: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void
}

const BROWSER_DEFAULTS: BrowserGeminiDependencies = {
  createWebSocket: (url) => new WebSocket(url),
  createAudioContext: () => new AudioContext(),
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: (timer) => clearTimeout(timer),
}

function safeCloseReason(reason: string): string {
  return reason
    .replace(/access_token=[^&\s]+/gi, 'access_token=[redacted]')
    .replace(/auth_tokens\/[\w./-]+/gi, 'auth_tokens/[redacted]')
    .slice(0, 160)
}

export class BrowserGeminiTransport implements GeminiTransport {
  private readonly stream: MediaStream | null
  private readonly dependencies: BrowserGeminiDependencies
  private socket: WebSocket | null = null
  private context: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private outputNodes = new Set<AudioBufferSourceNode>()
  private nextOutputTime = 0
  private learnerTranscript = ''
  private agentTranscript = ''
  private transcriptSequence = 0
  private muted = false
  private setupComplete = false
  private disposed = false
  private endedNotified = false
  private receiveQueue: Promise<void> = Promise.resolve()
  private setupTimer: ReturnType<typeof setTimeout> | null = null

  constructor(stream: MediaStream | null, dependencies: Partial<BrowserGeminiDependencies> = {}) {
    this.stream = stream
    this.dependencies = { ...BROWSER_DEFAULTS, ...dependencies }
  }

  async connect(token: string, model: string, context: SpeakingProviderContext, callbacks: GeminiTransportCallbacks) {
    this.disposed = false
    this.setupComplete = false
    this.endedNotified = false
    this.receiveQueue = Promise.resolve()
    const socket = this.dependencies.createWebSocket(geminiLiveWebSocketUrl(token))
    this.socket = socket
    await new Promise<void>((resolve, reject) => {
      let settled = false
      const rejectSetup = (error: GeminiProviderError) => {
        if (settled) return
        settled = true
        this.clearSetupTimer()
        reject(error)
      }
      const resolveSetup = () => {
        if (settled || this.disposed) return
        this.context = this.dependencies.createAudioContext()
        void this.context.resume()
        if (this.stream) this.startInput(socket)
        this.setupComplete = true
        settled = true
        this.clearSetupTimer()
        callbacks.status('listening')
        resolve()
      }

      this.setupTimer = this.dependencies.setTimeout(() => {
        rejectSetup(new GeminiProviderError('setup', 'Gemini Live setup did not complete within 10 seconds.'))
        this.close()
      }, SETUP_TIMEOUT_MS)

      socket.onopen = () => {
        socket.send(JSON.stringify({ setup: {
          model: `models/${model}`,
          generationConfig: { responseModalities: ['AUDIO'] },
          systemInstruction: { parts: [{ text: context.context.system_prompt }] },
          inputAudioTranscription: {}, outputAudioTranscription: {},
          tools: [{ functionDeclarations: [
            { name: 'get_due_chunks', parameters: { type: 'OBJECT', properties: { language: { type: 'STRING' }, limit: { type: 'INTEGER' } }, required: ['language'] } },
            { name: 'get_saved_words', parameters: { type: 'OBJECT', properties: { language: { type: 'STRING' }, limit: { type: 'INTEGER' } }, required: ['language'] } },
            { name: 'get_media_context', parameters: { type: 'OBJECT', properties: { library_item_id: { type: 'STRING' }, sentence_id: { type: 'INTEGER' } }, required: ['library_item_id'] } },
            { name: 'record_correction', parameters: { type: 'OBJECT' } },
            { name: 'suggest_expression', parameters: { type: 'OBJECT' } },
          ] }],
        } }))
      }
      socket.onmessage = (message) => {
        this.receiveQueue = this.receiveQueue.then(async () => {
          if (this.disposed) return
          const raw = await decodeGeminiServerMessage(message.data)
          let value: unknown
          try { value = JSON.parse(raw) } catch { throw new GeminiProviderError('message', 'Gemini Live returned malformed JSON.') }
          if (!value || typeof value !== 'object' || Array.isArray(value)) {
            throw new GeminiProviderError('message', 'Gemini Live returned an invalid server message.')
          }
          const record = value as Record<string, unknown>
          if ('setupComplete' in record && !this.setupComplete) resolveSetup()
          this.handleMessage(record, callbacks)
        }).catch((error: unknown) => {
          const failure = error instanceof GeminiProviderError
            ? error
            : new GeminiProviderError('message', 'Gemini Live server message decoding failed.')
          if (!this.setupComplete) {
            rejectSetup(failure)
            this.close()
          } else callbacks.error(failure)
        })
      }
      socket.onerror = () => {
        const error = new GeminiProviderError('websocket', 'Gemini Live WebSocket connection failed.')
        if (!this.setupComplete) rejectSetup(error)
        else callbacks.error(error)
      }
      socket.onclose = (event) => {
        if (this.disposed) return
        const close = { code: event.code, reason: safeCloseReason(event.reason) }
        if (!this.setupComplete) {
          rejectSetup(new GeminiProviderError('setup', 'Gemini Live closed before setup completed.', close))
          return
        }
        if (/quota|rate.?limit|resource.?exhausted|429/i.test(event.reason)) {
          callbacks.error(new GeminiProviderError('quota', 'Gemini provider quota or rate limit was reached.', close))
        } else if (event.code === 1000 || event.code === 1001) this.notifyEnded(callbacks)
        else callbacks.error(new GeminiProviderError('websocket', 'Gemini Live WebSocket closed unexpectedly.', close))
      }
    })
  }

  private startInput(socket: WebSocket) {
    const stream = this.stream
    if (!stream) return
    const context = this.context
    if (!context) return
    const source = context.createMediaStreamSource(stream)
    const processor = context.createScriptProcessor(4096, 1, 1)
    processor.onaudioprocess = (event) => {
      if (this.muted || socket.readyState !== WebSocket.OPEN) return
      const pcm = floatToPcm16(event.inputBuffer.getChannelData(0), context.sampleRate)
      socket.send(JSON.stringify({ realtimeInput: { audio: { data: bytesToBase64(pcm), mimeType: 'audio/pcm;rate=16000' } } }))
    }
    source.connect(processor)
    processor.connect(context.destination)
    this.source = source; this.processor = processor
  }

  private handleMessage(value: Record<string, unknown>, callbacks: GeminiTransportCallbacks) {
    const content = value.serverContent as Record<string, unknown> | undefined
    if (content?.interrupted) {
      this.stopOutput(); this.agentTranscript = ''; callbacks.interrupted(); callbacks.status('learner_speaking')
    }
    const inputTranscription = content?.inputTranscription as { text?: string } | undefined
    if (inputTranscription?.text) {
      this.learnerTranscript += inputTranscription.text
      callbacks.transcript({ role: 'learner', text: this.learnerTranscript, final: false })
    }
    const outputTranscription = content?.outputTranscription as { text?: string } | undefined
    if (outputTranscription?.text) {
      this.agentTranscript += outputTranscription.text
      callbacks.transcript({ role: 'agent', text: this.agentTranscript, final: false })
    }
    const toolCall = value.toolCall as { functionCalls?: Array<{ id: string; name: string; args?: Record<string, unknown> }> } | undefined
    for (const call of toolCall?.functionCalls ?? []) callbacks.tool({ id: call.id, name: call.name, arguments: call.args ?? {} })
    const modelTurn = content?.modelTurn as { parts?: Array<{ inlineData?: { data?: string } }> } | undefined
    for (const part of modelTurn?.parts ?? []) if (part.inlineData?.data) this.playPcm(part.inlineData.data, callbacks)
    if (content?.turnComplete) {
      this.emitFinalTranscripts(callbacks)
      callbacks.status('listening')
    }
    if (value.goAway && this.setupComplete) this.notifyEnded(callbacks)
  }

  private notifyEnded(callbacks: GeminiTransportCallbacks) {
    if (this.endedNotified) return
    this.endedNotified = true
    callbacks.ended()
  }

  private playPcm(encoded: string, callbacks: GeminiTransportCallbacks) {
    if (!this.context) return
    callbacks.status('agent_speaking')
    const binary = atob(encoded)
    const bytes = Uint8Array.from(binary, (value) => value.charCodeAt(0))
    const samples = new Int16Array(bytes.buffer)
    const buffer = this.context.createBuffer(1, samples.length, 24_000)
    const output = buffer.getChannelData(0)
    for (let index = 0; index < samples.length; index += 1) output[index] = samples[index] / 32768
    const node = this.context.createBufferSource(); node.buffer = buffer; node.connect(this.context.destination)
    const startAt = Math.max(this.context.currentTime, this.nextOutputTime)
    this.nextOutputTime = startAt + buffer.duration
    this.outputNodes.add(node); node.onended = () => this.outputNodes.delete(node); node.start(startAt)
  }

  private emitFinalTranscripts(callbacks: GeminiTransportCallbacks) {
    this.transcriptSequence += 1
    const providerTurnId = `gemini-turn-${this.transcriptSequence}`
    const learner = this.learnerTranscript.trim()
    const agent = this.agentTranscript.trim()
    if (learner) callbacks.transcript({ role: 'learner', text: learner, final: true, providerTurnId: `${providerTurnId}-learner` })
    if (agent) callbacks.transcript({ role: 'agent', text: agent, final: true, providerTurnId: `${providerTurnId}-agent` })
    this.learnerTranscript = ''; this.agentTranscript = ''
  }

  mute(muted: boolean) { this.muted = muted }
  sendText(text: string) { this.requireReady(); this.socket?.send(JSON.stringify({ clientContent: { turns: [{ role: 'user', parts: [{ text }] }], turnComplete: true } })) }
  interrupt() { this.requireReady(); this.socket?.send(JSON.stringify({ clientContent: { turnComplete: true } })); this.stopOutput() }
  respondToTool(id: string, result: unknown) { this.requireReady(); this.socket?.send(JSON.stringify({ toolResponse: { functionResponses: [{ id, response: { result } }] } })) }
  private requireReady() {
    if (!this.setupComplete) throw new GeminiProviderError('setup', 'Gemini Live setup is not complete.')
  }
  private clearSetupTimer() {
    if (this.setupTimer !== null) this.dependencies.clearTimeout(this.setupTimer)
    this.setupTimer = null
  }
  private stopOutput() {
    for (const node of this.outputNodes) { try { node.stop() } catch { /* already stopped */ } }
    this.outputNodes.clear(); this.nextOutputTime = this.context?.currentTime ?? 0
  }
  close() {
    this.disposed = true; this.setupComplete = false; this.clearSetupTimer()
    this.stopOutput(); this.processor?.disconnect(); this.source?.disconnect(); void this.context?.close()
    this.processor = null; this.source = null; this.context = null
    this.learnerTranscript = ''; this.agentTranscript = ''; this.nextOutputTime = 0
    if (this.socket?.readyState === WebSocket.CONNECTING) this.socket.onopen = () => this.socket?.close()
    else if (this.socket?.readyState === WebSocket.OPEN) this.socket.close()
    this.socket = null
  }
}

const DEFAULTS: GeminiDependencies = {
  fetchToken: createGeminiToken,
  getUserMedia: () => navigator.mediaDevices?.getUserMedia
    ? navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    : Promise.reject(new Error('No microphone is available in this browser.')),
  createTransport: (stream) => new BrowserGeminiTransport(stream),
}

export class GeminiLiveProvider implements SpeakingAgentProvider {
  readonly id = 'gemini-live' as const
  private events = new ProviderEvents()
  private dependencies: GeminiDependencies
  private stream: MediaStream | null = null
  private transport: GeminiTransport | null = null
  private connected = false
  constructor(dependencies: Partial<GeminiDependencies> = {}) { this.dependencies = { ...DEFAULTS, ...dependencies } }
  async connect(context: SpeakingProviderContext) {
    this.events.emit('status', 'connecting')
    try {
      let credential
      try {
        credential = await this.dependencies.fetchToken()
      } catch (error) {
        if (error instanceof SpeakingApiError && error.status === 429) {
          throw new GeminiProviderError('quota', 'Gemini provider quota or rate limit was reached.')
        }
        throw new GeminiProviderError('credential', 'Gemini credential request failed.')
      }
      if (!context.textOnly) {
        try {
          this.stream = await this.dependencies.getUserMedia()
        } catch (error) {
          const name = error instanceof DOMException ? error.name : (error as { name?: string })?.name
          if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
            throw new GeminiProviderError('microphone_unavailable', 'No microphone is available.')
          }
          throw new GeminiProviderError('microphone_permission', 'Microphone permission was denied.')
        }
      }
      this.transport = this.dependencies.createTransport(this.stream)
      try {
        await this.transport.connect(credential.value, credential.model, context, {
        status: (status) => this.events.emit('status', status), transcript: (event) => this.events.emit('transcript', event),
        tool: (event) => this.events.emit('tool', event), interrupted: () => { this.events.emit('status', 'interrupted'); this.events.emit('interrupted') },
        error: (error) => this.events.emit('error', error instanceof GeminiProviderError ? error : new GeminiProviderError('websocket', 'Gemini Live WebSocket connection failed.')),
        ended: () => { if (this.connected) this.events.emit('ended', 'provider') },
        })
      } catch (error) {
        if (error instanceof GeminiProviderError) throw error
        throw new GeminiProviderError('websocket', 'Gemini Live WebSocket connection failed.')
      }
      this.connected = true
    } catch (error) { this.release(); this.events.emit('status', 'idle'); throw error }
  }
  async disconnect(reason: SpeakingEndReason = 'user') { const wasConnected = this.connected; this.release(); this.events.emit('status', 'ended'); if (wasConnected) this.events.emit('ended', reason) }
  mute(muted: boolean) { this.transport?.mute(muted); this.events.emit('status', muted ? 'muted' : 'listening') }
  async sendText(text: string) { this.transport?.sendText(text) }
  interrupt() { this.transport?.interrupt() }
  respondToTool(id: string, result: unknown) { this.transport?.respondToTool(id, result) }
  onStatus = (listener: (status: SpeakingConnectionStatus) => void) => this.events.on('status', listener)
  onTranscript = (listener: (event: SpeakingTranscriptEvent) => void) => this.events.on('transcript', listener)
  onToolCall = (listener: (event: SpeakingToolCall) => void) => this.events.on('tool', listener)
  onInterrupted = (listener: () => void) => this.events.on('interrupted', listener)
  onError = (listener: (error: Error) => void) => this.events.on('error', listener)
  onEnded = (listener: (reason: SpeakingEndReason) => void) => this.events.on('ended', listener)
  private release() { this.connected = false; this.transport?.close(); this.transport = null; this.stream?.getTracks().forEach((track) => track.stop()); this.stream = null }
}
