import type { RealtimeItem } from '@openai/agents/realtime'

import {
  createRealtimeClientCredential,
  type RealtimeClientCredential,
} from '../api/realtime'
import type { VoiceTranscriptEvent } from './voiceLiveDialogueProvider'
import {
  initialVoiceDialogueStatus,
  MAX_REALTIME_LEARNER_TURNS,
  MAX_REALTIME_SESSION_MS,
  type Unsubscribe,
  type VoiceDialogueContext,
  type VoiceDialogueStatus,
  type VoiceEndReason,
  type VoiceLiveDialogueProvider,
} from './voiceLiveDialogueProvider'

interface SessionCallbacks {
  onHistory: (items: RealtimeItem[]) => void
  onPartnerSpeaking: (speaking: boolean) => void
  onLearnerSpeaking: (speaking: boolean) => void
  onInterrupted: () => void
  onTranscriptUnavailable: () => void
  onDisconnected: () => void
  onError: (error: Error) => void
}

export interface RealtimeSessionAdapter {
  connect(clientSecret: string): Promise<void>
  requestInitialResponse(): void
  mute(muted: boolean): void
  close(): void
  subscribe(callbacks: SessionCallbacks): Unsubscribe
}

export interface OpenAIRealtimeDependencies {
  fetchCredential: () => Promise<RealtimeClientCredential>
  getUserMedia: () => Promise<MediaStream>
  createSession: (
    credential: RealtimeClientCredential,
    context: VoiceDialogueContext,
    mediaStream: MediaStream,
  ) => RealtimeSessionAdapter | Promise<RealtimeSessionAdapter>
  setTimeout: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void
}

type ListenerMap = {
  transcript: (event: VoiceTranscriptEvent) => void
  status: (status: VoiceDialogueStatus) => void
  interrupted: () => void
  notice: (message: string) => void
  error: (error: Error) => void
  ended: (reason: VoiceEndReason) => void
}

function toError(value: unknown, fallback: string): Error {
  if (value instanceof Error) {
    return value
  }
  return new Error(fallback)
}

export function transcriptFromRealtimeItem(
  item: RealtimeItem,
): VoiceTranscriptEvent | null {
  if (item.type !== 'message' || item.role === 'system' || item.status !== 'completed') {
    return null
  }

  const text = item.content
    .map((content) => {
      if (content.type === 'input_text' || content.type === 'output_text') {
        return content.text
      }
      if (content.type === 'input_audio' || content.type === 'output_audio') {
        return content.transcript || ''
      }
      return ''
    })
    .join(' ')
    .trim()

  if (!text) {
    return null
  }

  return {
    id: item.itemId,
    role: item.role === 'user' ? 'learner' : 'partner',
    text,
  }
}

async function createSdkSession(
  credential: RealtimeClientCredential,
  context: VoiceDialogueContext,
  mediaStream: MediaStream,
): Promise<RealtimeSessionAdapter> {
  const {
    OpenAIRealtimeWebRTC,
    RealtimeAgent,
    RealtimeSession,
  } = await import('@openai/agents/realtime')
  const transport = new OpenAIRealtimeWebRTC({ mediaStream })
  const agent = new RealtimeAgent({
    name: 'Language learning conversation partner',
    instructions: context.instructions,
  })
  const session = new RealtimeSession(agent, {
    model: credential.model,
    transport,
    tracingDisabled: true,
    config: {
      outputModalities: ['audio'],
      audio: {
        input: {
          transcription: {
            model: 'gpt-4o-mini-transcribe',
            language: context.lesson.source_language,
          },
          turnDetection: {
            type: 'semantic_vad',
            createResponse: true,
            interruptResponse: true,
          },
        },
        output: { voice: credential.voice },
      },
    },
  })

  return {
    connect: (clientSecret) => session.connect({ apiKey: clientSecret }),
    requestInitialResponse: () => {
      session.transport.sendEvent({ type: 'response.create' })
    },
    mute: (muted) => session.mute(muted),
    close: () => session.close(),
    subscribe: (callbacks) => {
      const onHistory = (items: RealtimeItem[]) => callbacks.onHistory(items)
      const onAudioStart = () => callbacks.onPartnerSpeaking(true)
      const onAudioStopped = () => callbacks.onPartnerSpeaking(false)
      const onInterrupted = () => callbacks.onInterrupted()
      const onError = (event: { error: unknown }) => {
        callbacks.onError(toError(event.error, 'The realtime session failed.'))
      }
      const onTransportEvent = (event: { type: string }) => {
        if (event.type === 'input_audio_buffer.speech_started') {
          callbacks.onLearnerSpeaking(true)
        } else if (event.type === 'input_audio_buffer.speech_stopped') {
          callbacks.onLearnerSpeaking(false)
        } else if (event.type === 'conversation.item.input_audio_transcription.failed') {
          callbacks.onTranscriptUnavailable()
        }
      }
      const onConnectionChange = (status: string) => {
        if (status === 'disconnected') {
          callbacks.onDisconnected()
        }
      }

      session.on('history_updated', onHistory)
      session.on('audio_start', onAudioStart)
      session.on('audio_stopped', onAudioStopped)
      session.on('audio_interrupted', onInterrupted)
      session.on('error', onError)
      session.on('transport_event', onTransportEvent)
      session.transport.on('connection_change', onConnectionChange)

      return () => {
        session.off('history_updated', onHistory)
        session.off('audio_start', onAudioStart)
        session.off('audio_stopped', onAudioStopped)
        session.off('audio_interrupted', onInterrupted)
        session.off('error', onError)
        session.off('transport_event', onTransportEvent)
        session.transport.off('connection_change', onConnectionChange)
      }
    },
  }
}

const DEFAULT_DEPENDENCIES: OpenAIRealtimeDependencies = {
  fetchCredential: createRealtimeClientCredential,
  getUserMedia: () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      return Promise.reject(new Error('Microphone access is not supported in this browser.'))
    }
    return navigator.mediaDevices.getUserMedia({ audio: true })
  },
  createSession: createSdkSession,
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: (timer) => clearTimeout(timer),
}

export class OpenAIRealtimeDialogueProvider implements VoiceLiveDialogueProvider {
  private readonly dependencies: OpenAIRealtimeDependencies
  private readonly listeners = {
    transcript: new Set<ListenerMap['transcript']>(),
    status: new Set<ListenerMap['status']>(),
    interrupted: new Set<ListenerMap['interrupted']>(),
    notice: new Set<ListenerMap['notice']>(),
    error: new Set<ListenerMap['error']>(),
    ended: new Set<ListenerMap['ended']>(),
  }
  private status = initialVoiceDialogueStatus()
  private session: RealtimeSessionAdapter | null = null
  private mediaStream: MediaStream | null = null
  private unsubscribeSession: Unsubscribe | null = null
  private sessionTimer: ReturnType<typeof setTimeout> | null = null
  private emittedTranscripts = new Map<string, string>()
  private countedLearnerIds = new Set<string>()
  private learnerTurns = 0
  private turnLimitPending = false
  private connected = false
  private disconnecting = false

  constructor(dependencies: Partial<OpenAIRealtimeDependencies> = {}) {
    this.dependencies = { ...DEFAULT_DEPENDENCIES, ...dependencies }
  }

  async connect(context: VoiceDialogueContext): Promise<void> {
    if (this.session || this.connected) {
      throw new Error('A realtime conversation is already active.')
    }

    this.resetRuntimeState()
    this.updateStatus({
      connection: 'connecting',
      microphone: 'inactive',
      partner: 'waiting',
      learner: 'waiting',
      conversation: 'idle',
    })

    try {
      const credential = await this.dependencies.fetchCredential()
      this.mediaStream = await this.dependencies.getUserMedia()
      this.session = await this.dependencies.createSession(
        credential,
        context,
        this.mediaStream,
      )
      this.unsubscribeSession = this.session.subscribe({
        onHistory: (items) => this.handleHistory(items),
        onPartnerSpeaking: (speaking) => {
          this.updateStatus({ partner: speaking ? 'speaking' : 'waiting' })
        },
        onLearnerSpeaking: (speaking) => {
          this.updateStatus({ learner: speaking ? 'speaking' : 'waiting' })
        },
        onInterrupted: () => {
          this.updateStatus({ partner: 'interrupted' })
          this.emit('interrupted')
        },
        onTranscriptUnavailable: () => {
          this.emit('notice', 'A learner transcript was unavailable for one turn.')
        },
        onDisconnected: () => {
          if (this.connected && !this.disconnecting) {
            this.fail(new Error('The realtime provider disconnected unexpectedly.'))
          }
        },
        onError: (error) => this.fail(error),
      })
      await this.session.connect(credential.value)
      this.connected = true
      this.updateStatus({
        connection: 'connected',
        microphone: 'listening',
        partner: 'waiting',
        learner: 'waiting',
        conversation: 'active',
      })
      this.sessionTimer = this.dependencies.setTimeout(() => {
        void this.disconnect('time_limit')
      }, MAX_REALTIME_SESSION_MS)
      this.session.requestInitialResponse()
    } catch (error) {
      this.releaseResources()
      this.updateStatus({
        connection: 'disconnected',
        microphone: 'inactive',
        partner: 'waiting',
        conversation: 'idle',
      })
      throw toError(error, 'Unable to start the realtime voice conversation.')
    }
  }

  async disconnect(reason: VoiceEndReason = 'user'): Promise<void> {
    if (this.disconnecting) {
      return
    }
    this.disconnecting = true
    this.updateStatus({ conversation: 'ending' })
    this.releaseResources()
    this.updateStatus({
      connection: 'disconnected',
      microphone: 'inactive',
      partner: 'waiting',
      learner: 'waiting',
      conversation: 'ended',
    })
    this.emit('ended', reason)
    this.disconnecting = false
  }

  mute(muted: boolean): void {
    if (!this.session || !this.connected) {
      return
    }
    this.session.mute(muted)
    this.updateStatus({ microphone: muted ? 'muted' : 'listening' })
  }

  onTranscript(listener: ListenerMap['transcript']): Unsubscribe {
    return this.addListener('transcript', listener)
  }

  onStatusChange(listener: ListenerMap['status']): Unsubscribe {
    listener({ ...this.status })
    return this.addListener('status', listener)
  }

  onInterrupted(listener: ListenerMap['interrupted']): Unsubscribe {
    return this.addListener('interrupted', listener)
  }

  onNotice(listener: ListenerMap['notice']): Unsubscribe {
    return this.addListener('notice', listener)
  }

  onError(listener: ListenerMap['error']): Unsubscribe {
    return this.addListener('error', listener)
  }

  onEnded(listener: ListenerMap['ended']): Unsubscribe {
    return this.addListener('ended', listener)
  }

  private handleHistory(items: RealtimeItem[]) {
    for (const item of items) {
      const transcript = transcriptFromRealtimeItem(item)
      if (!transcript || this.emittedTranscripts.get(transcript.id) === transcript.text) {
        continue
      }
      this.emittedTranscripts.set(transcript.id, transcript.text)
      this.emit('transcript', transcript)

      if (transcript.role === 'learner' && !this.countedLearnerIds.has(transcript.id)) {
        this.countedLearnerIds.add(transcript.id)
        this.learnerTurns += 1
        if (this.learnerTurns >= MAX_REALTIME_LEARNER_TURNS) {
          this.turnLimitPending = true
          this.emit('notice', 'The five-turn practice limit was reached. Finishing after the partner response.')
        }
      } else if (this.turnLimitPending) {
        void this.disconnect('turn_limit')
      }
    }
  }

  private fail(error: Error) {
    this.emit('error', error)
    this.releaseResources()
    this.updateStatus({
      connection: 'disconnected',
      microphone: 'inactive',
      partner: 'waiting',
      learner: 'waiting',
      conversation: 'idle',
    })
  }

  private releaseResources() {
    if (this.sessionTimer !== null) {
      this.dependencies.clearTimeout(this.sessionTimer)
      this.sessionTimer = null
    }
    this.unsubscribeSession?.()
    this.unsubscribeSession = null
    this.session?.close()
    this.session = null
    this.mediaStream?.getTracks().forEach((track) => track.stop())
    this.mediaStream = null
    this.connected = false
  }

  private resetRuntimeState() {
    this.emittedTranscripts.clear()
    this.countedLearnerIds.clear()
    this.learnerTurns = 0
    this.turnLimitPending = false
    this.disconnecting = false
  }

  private updateStatus(update: Partial<VoiceDialogueStatus>) {
    this.status = { ...this.status, ...update }
    this.emit('status', { ...this.status })
  }

  private addListener<K extends keyof ListenerMap>(
    type: K,
    listener: ListenerMap[K],
  ): Unsubscribe {
    const listeners = this.listeners[type] as Set<ListenerMap[K]>
    listeners.add(listener)
    return () => listeners.delete(listener)
  }

  private emit<K extends keyof ListenerMap>(
    type: K,
    ...args: Parameters<ListenerMap[K]>
  ) {
    const listeners = this.listeners[type] as Set<ListenerMap[K]>
    listeners.forEach((listener) => {
      const invoke = listener as (...listenerArgs: Parameters<ListenerMap[K]>) => void
      invoke(...args)
    })
  }
}

export function createConfiguredRealtimeDialogueProvider(): VoiceLiveDialogueProvider | undefined {
  return import.meta.env.VITE_LIVE_DIALOGUE_PROVIDER === 'realtime'
    ? new OpenAIRealtimeDialogueProvider()
    : undefined
}
