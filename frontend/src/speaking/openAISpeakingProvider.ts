import { OpenAIRealtimeDialogueProvider } from '../training/openAIRealtimeDialogueProvider'
import type { VoiceLiveDialogueProvider } from '../training/voiceLiveDialogueProvider'
import type { Lesson } from '../types/lesson'
import type { SpeakingEndReason } from '../types/speaking'
import { ProviderEvents, type SpeakingAgentProvider, type SpeakingProviderContext } from './provider'

export class OpenAISpeakingProvider implements SpeakingAgentProvider {
  readonly id = 'openai-realtime' as const
  private events = new ProviderEvents()
  private provider: VoiceLiveDialogueProvider | null = null
  private unsubs: Array<() => void> = []
  private factory: (duration: number) => VoiceLiveDialogueProvider

  constructor(factory: (duration: number) => VoiceLiveDialogueProvider = (duration: number) => new OpenAIRealtimeDialogueProvider({}, { maxSessionMs: duration * 1_000, maxLearnerTurns: Number.MAX_SAFE_INTEGER })) {
    this.factory = factory
  }

  async connect(value: SpeakingProviderContext) {
    if (value.textOnly) throw new Error('OpenAI text-only Speaking is not available in this adapter.')
    this.events.emit('status', 'connecting')
    const provider = this.factory(value.durationSeconds)
    this.provider = provider
    this.unsubs = [
      provider.onTranscript((event) => this.events.emit('transcript', { role: event.role === 'learner' ? 'learner' : 'agent', text: event.text, final: true, providerTurnId: event.id })),
      provider.onStatusChange((status) => {
        if (status.microphone === 'muted') this.events.emit('status', 'muted')
        else if (status.learner === 'speaking') this.events.emit('status', 'learner_speaking')
        else if (status.partner === 'speaking') this.events.emit('status', 'agent_speaking')
        else if (status.connection === 'connected') this.events.emit('status', 'listening')
      }),
      provider.onInterrupted(() => { this.events.emit('status', 'interrupted'); this.events.emit('interrupted') }),
      provider.onError((error) => this.events.emit('error', error)),
      provider.onEnded((reason) => this.events.emit('ended', reason === 'time_limit' ? 'time_limit' : 'provider')),
    ]
    const context = value.context
    const lesson: Lesson = {
      id: 'speaking-agent', title: context.media_title ?? 'Speaking session', source_url: 'local:speaking-agent',
      source_language: context.language, target_language: 'zh-CN', level: context.level, audio_url: null,
      source: 'subtitles', sentences: [],
      training_plan: { active_recall: { enabled: false, count: 0 }, blind_listening: { enabled: false }, comprehension: { enabled: false }, mini_dictation: { enabled: false }, shadowing: { enabled: false }, retell: { enabled: false }, live_dialogue: { enabled: true } },
      training_order: ['active_recall', 'blind_listening', 'comprehension', 'mini_dictation', 'shadowing', 'retell', 'live_dialogue'],
      ai: { provider: 'speaking-agent', status: 'complete' },
    }
    try {
      await provider.connect({
        lesson, instructions: context.system_prompt,
        scenario: { id: 'speaking-agent', title: 'Speaking Agent', description: context.session_goal, learnerGoal: context.session_goal },
        candidateChunks: context.target_chunks.map((target) => ({ id: target.study_item_id, text: target.text, meaning: target.meaning })),
      })
    } catch (error) { this.cleanup(); throw error }
  }

  async disconnect(reason: SpeakingEndReason = 'user') { await this.provider?.disconnect(reason === 'time_limit' ? 'time_limit' : 'user'); this.cleanup(); this.events.emit('status', 'ended') }
  mute(muted: boolean) { this.provider?.mute(muted) }
  onStatus = (listener: (status: import('./provider').SpeakingConnectionStatus) => void) => this.events.on('status', listener)
  onTranscript = (listener: (event: import('./provider').SpeakingTranscriptEvent) => void) => this.events.on('transcript', listener)
  onToolCall = (listener: (event: import('./provider').SpeakingToolCall) => void) => this.events.on('tool', listener)
  onInterrupted = (listener: () => void) => this.events.on('interrupted', listener)
  onError = (listener: (error: Error) => void) => this.events.on('error', listener)
  onEnded = (listener: (reason: SpeakingEndReason) => void) => this.events.on('ended', listener)
  private cleanup() { this.unsubs.forEach((unsubscribe) => unsubscribe()); this.unsubs = []; this.provider = null }
}
