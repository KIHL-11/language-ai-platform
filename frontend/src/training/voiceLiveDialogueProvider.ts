import type { DialogueChunk, DialogueMessage, DialogueScenario } from './liveDialogue'
import type { Lesson } from '../types/lesson'

export const MAX_REALTIME_LEARNER_TURNS = 5
export const MAX_REALTIME_SESSION_MS = 3 * 60 * 1000

export type VoiceConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'disconnected'

export type VoiceMicrophoneStatus = 'inactive' | 'listening' | 'muted'
export type VoicePartnerStatus = 'waiting' | 'speaking' | 'interrupted'
export type VoiceLearnerStatus = 'waiting' | 'speaking'
export type VoiceConversationStatus = 'idle' | 'active' | 'ending' | 'ended'
export type VoiceEndReason = 'user' | 'turn_limit' | 'time_limit' | 'stage_exit'

export interface VoiceDialogueStatus {
  connection: VoiceConnectionStatus
  microphone: VoiceMicrophoneStatus
  partner: VoicePartnerStatus
  learner: VoiceLearnerStatus
  conversation: VoiceConversationStatus
}

export interface VoiceDialogueContext {
  lesson: Lesson
  scenario: DialogueScenario
  candidateChunks: DialogueChunk[]
  instructions: string
}

export interface VoiceTranscriptEvent extends DialogueMessage {
  role: 'partner' | 'learner'
}

export type Unsubscribe = () => void

export interface VoiceLiveDialogueProvider {
  connect(context: VoiceDialogueContext): Promise<void>
  disconnect(reason?: VoiceEndReason): Promise<void>
  mute(muted: boolean): void
  onTranscript(listener: (event: VoiceTranscriptEvent) => void): Unsubscribe
  onStatusChange(listener: (status: VoiceDialogueStatus) => void): Unsubscribe
  onInterrupted(listener: () => void): Unsubscribe
  onNotice(listener: (message: string) => void): Unsubscribe
  onError(listener: (error: Error) => void): Unsubscribe
  onEnded(listener: (reason: VoiceEndReason) => void): Unsubscribe
}

export function initialVoiceDialogueStatus(): VoiceDialogueStatus {
  return {
    connection: 'idle',
    microphone: 'inactive',
    partner: 'waiting',
    learner: 'waiting',
    conversation: 'idle',
  }
}
