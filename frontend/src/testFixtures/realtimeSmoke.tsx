import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import '../index.css'
import TrainingSession from '../pages/TrainingSession'
import type { Lesson } from '../types/lesson'
import {
  initialVoiceDialogueStatus,
  type Unsubscribe,
  type VoiceDialogueContext,
  type VoiceDialogueStatus,
  type VoiceEndReason,
  type VoiceLiveDialogueProvider,
  type VoiceTranscriptEvent,
} from '../training/voiceLiveDialogueProvider'

class BrowserMockVoiceProvider implements VoiceLiveDialogueProvider {
  private transcripts = new Set<(event: VoiceTranscriptEvent) => void>()
  private statuses = new Set<(status: VoiceDialogueStatus) => void>()
  private interruptions = new Set<() => void>()
  private notices = new Set<(message: string) => void>()
  private errors = new Set<(error: Error) => void>()
  private endings = new Set<(reason: VoiceEndReason) => void>()

  async connect(_context: VoiceDialogueContext) {
    const status: VoiceDialogueStatus = {
      ...initialVoiceDialogueStatus(),
      connection: 'connected',
      microphone: 'listening',
      partner: 'interrupted',
      conversation: 'active',
    }
    this.statuses.forEach((listener) => listener(status))
    this.transcripts.forEach((listener) => listener({
      id: 'mock-partner-1',
      role: 'partner',
      text: 'Tell me about a new place you visited.',
    }))
    this.transcripts.forEach((listener) => listener({
      id: 'mock-learner-1',
      role: 'learner',
      text: 'Here we are in front of a new museum.',
    }))
    this.interruptions.forEach((listener) => listener())
  }

  async disconnect(reason: VoiceEndReason = 'user') {
    this.endings.forEach((listener) => listener(reason))
  }

  mute(muted: boolean) {
    const status: VoiceDialogueStatus = {
      ...initialVoiceDialogueStatus(),
      connection: 'connected',
      microphone: muted ? 'muted' : 'listening',
      conversation: 'active',
    }
    this.statuses.forEach((listener) => listener(status))
  }

  onTranscript(listener: (event: VoiceTranscriptEvent) => void): Unsubscribe {
    this.transcripts.add(listener)
    return () => this.transcripts.delete(listener)
  }

  onStatusChange(listener: (status: VoiceDialogueStatus) => void): Unsubscribe {
    listener(initialVoiceDialogueStatus())
    this.statuses.add(listener)
    return () => this.statuses.delete(listener)
  }

  onInterrupted(listener: () => void): Unsubscribe {
    this.interruptions.add(listener)
    return () => this.interruptions.delete(listener)
  }

  onNotice(listener: (message: string) => void): Unsubscribe {
    this.notices.add(listener)
    return () => this.notices.delete(listener)
  }

  onError(listener: (error: Error) => void): Unsubscribe {
    this.errors.add(listener)
    return () => this.errors.delete(listener)
  }

  onEnded(listener: (reason: VoiceEndReason) => void): Unsubscribe {
    this.endings.add(listener)
    return () => this.endings.delete(listener)
  }
}

const lesson: Lesson = {
  id: 'realtime-mock-smoke',
  title: 'Me at the zoo',
  source_url: 'local-fixture',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [{
    id: 1,
    text: 'Here we are in front of the elephants.',
    translation: 'We are standing by the elephants.',
    start: 0,
    end: 4,
    keywords: [],
    chunks: [
      { text: 'here we are', meaning: 'we have arrived', usage: '' },
      { text: 'in front of', meaning: 'before or ahead of', usage: '' },
    ],
    grammar: '',
    training: { blind_listening: true, dictation: true, shadowing: true, retell: true },
    ai_status: 'ok',
  }],
  training_plan: {
    active_recall: { enabled: false, count: 0 },
    blind_listening: { enabled: false },
    comprehension: { enabled: false },
    mini_dictation: { enabled: false },
    shadowing: { enabled: false },
    retell: { enabled: false },
    live_dialogue: { enabled: true },
  },
  training_order: ['live_dialogue'],
  ai: { provider: 'mock', status: 'complete' },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <main className="mx-auto min-h-screen max-w-6xl px-5 py-10 sm:px-8">
      <TrainingSession
        lesson={lesson}
        audioUrl={null}
        onExit={() => undefined}
        voiceLiveDialogueProvider={new BrowserMockVoiceProvider()}
      />
    </main>
  </StrictMode>,
)
