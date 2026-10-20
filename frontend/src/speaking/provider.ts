import type { SpeakingContext, SpeakingEndReason, SpeakingProviderId } from '../types/speaking'

export type Unsubscribe = () => void
export type TranscriptRole = 'learner' | 'agent'
export interface SpeakingTranscriptEvent { role: TranscriptRole; text: string; final: boolean; providerTurnId?: string; sequence?: number }
export interface SpeakingToolCall { id: string; name: string; arguments: Record<string, unknown> }
export type SpeakingConnectionStatus = 'idle' | 'connecting' | 'listening' | 'agent_speaking' | 'learner_speaking' | 'interrupted' | 'muted' | 'ended'
export interface SpeakingProviderContext { context: SpeakingContext; durationSeconds: number; textOnly?: boolean }
export interface SpeakingAgentProvider {
  readonly id: SpeakingProviderId
  connect(context: SpeakingProviderContext): Promise<void>
  disconnect(reason?: SpeakingEndReason): Promise<void>
  mute(muted: boolean): void
  sendText?(text: string): Promise<void>
  interrupt?(): void
  respondToTool?(id: string, result: unknown): void
  onStatus(listener: (status: SpeakingConnectionStatus) => void): Unsubscribe
  onTranscript(listener: (event: SpeakingTranscriptEvent) => void): Unsubscribe
  onToolCall(listener: (event: SpeakingToolCall) => void): Unsubscribe
  onInterrupted(listener: () => void): Unsubscribe
  onError(listener: (error: Error) => void): Unsubscribe
  onEnded(listener: (reason: SpeakingEndReason) => void): Unsubscribe
}

export type ProviderListenerMap = {
  status: (status: SpeakingConnectionStatus) => void
  transcript: (event: SpeakingTranscriptEvent) => void
  tool: (event: SpeakingToolCall) => void
  interrupted: () => void
  error: (error: Error) => void
  ended: (reason: SpeakingEndReason) => void
}

export class ProviderEvents {
  private listeners = {
    status: new Set<ProviderListenerMap['status']>(), transcript: new Set<ProviderListenerMap['transcript']>(),
    tool: new Set<ProviderListenerMap['tool']>(), interrupted: new Set<ProviderListenerMap['interrupted']>(),
    error: new Set<ProviderListenerMap['error']>(), ended: new Set<ProviderListenerMap['ended']>(),
  }
  on<K extends keyof ProviderListenerMap>(kind: K, listener: ProviderListenerMap[K]): Unsubscribe {
    ;(this.listeners[kind] as Set<ProviderListenerMap[K]>).add(listener)
    return () => (this.listeners[kind] as Set<ProviderListenerMap[K]>).delete(listener)
  }
  emit<K extends keyof ProviderListenerMap>(kind: K, ...args: Parameters<ProviderListenerMap[K]>) {
    for (const listener of this.listeners[kind]) (listener as (...values: Parameters<ProviderListenerMap[K]>) => void)(...args)
  }
  clear() { Object.values(this.listeners).forEach((values) => values.clear()) }
}
