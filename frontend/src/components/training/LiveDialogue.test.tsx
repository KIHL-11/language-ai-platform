import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  LocalMockDialogueProvider,
  type LiveDialogueProvider,
  type LiveDialogueProviderContext,
  type ProviderTurn,
} from '../../training/liveDialogueProvider'
import type { Lesson } from '../../types/lesson'
import {
  initialVoiceDialogueStatus,
  type Unsubscribe,
  type VoiceDialogueContext,
  type VoiceDialogueStatus,
  type VoiceEndReason,
  type VoiceLiveDialogueProvider,
  type VoiceTranscriptEvent,
} from '../../training/voiceLiveDialogueProvider'
import LiveDialogue from './LiveDialogue'

const lesson: Lesson = {
  id: 'dialogue-component',
  title: 'Making weekend plans',
  source_url: 'https://example.com/lesson',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [{
    id: 1,
    text: 'Would you like to get coffee this weekend?',
    translation: '这个周末你想喝咖啡吗？',
    start: 0,
    end: 3,
    keywords: [],
    chunks: [
      { text: 'would you like to', meaning: '你想不想', usage: '' },
      { text: 'this weekend', meaning: '这个周末', usage: '' },
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

class FakeVoiceProvider implements VoiceLiveDialogueProvider {
  private transcriptListeners = new Set<(event: VoiceTranscriptEvent) => void>()
  private statusListeners = new Set<(status: VoiceDialogueStatus) => void>()
  private interruptedListeners = new Set<() => void>()
  private noticeListeners = new Set<(message: string) => void>()
  private errorListeners = new Set<(error: Error) => void>()
  private endedListeners = new Set<(reason: VoiceEndReason) => void>()

  connect = vi.fn(async (_context: VoiceDialogueContext) => {
    this.emitStatus({
      ...initialVoiceDialogueStatus(),
      connection: 'connected',
      microphone: 'listening',
      conversation: 'active',
    })
  })

  disconnect = vi.fn(async (reason: VoiceEndReason = 'user') => {
    this.endedListeners.forEach((listener) => listener(reason))
  })

  mute = vi.fn((muted: boolean) => {
    this.emitStatus({
      ...initialVoiceDialogueStatus(),
      connection: 'connected',
      microphone: muted ? 'muted' : 'listening',
      conversation: 'active',
    })
  })

  onTranscript(listener: (event: VoiceTranscriptEvent) => void): Unsubscribe {
    this.transcriptListeners.add(listener)
    return () => this.transcriptListeners.delete(listener)
  }

  onStatusChange(listener: (status: VoiceDialogueStatus) => void): Unsubscribe {
    listener(initialVoiceDialogueStatus())
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  onInterrupted(listener: () => void): Unsubscribe {
    this.interruptedListeners.add(listener)
    return () => this.interruptedListeners.delete(listener)
  }

  onNotice(listener: (message: string) => void): Unsubscribe {
    this.noticeListeners.add(listener)
    return () => this.noticeListeners.delete(listener)
  }

  onError(listener: (error: Error) => void): Unsubscribe {
    this.errorListeners.add(listener)
    return () => this.errorListeners.delete(listener)
  }

  onEnded(listener: (reason: VoiceEndReason) => void): Unsubscribe {
    this.endedListeners.add(listener)
    return () => this.endedListeners.delete(listener)
  }

  emitTranscript(event: VoiceTranscriptEvent) {
    this.transcriptListeners.forEach((listener) => listener(event))
  }

  emitInterrupted() {
    this.interruptedListeners.forEach((listener) => listener())
  }

  private emitStatus(status: VoiceDialogueStatus) {
    this.statusListeners.forEach((listener) => listener(status))
  }
}

async function finishConversation(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Start Conversation' }))

  const responses = [
    'Would you like to meet this weekend?',
    'The detail matters because my friends are busy.',
    'I would make plans earlier next time.',
  ]
  for (const response of responses) {
    await user.type(screen.getByLabelText('Your response'), response)
    await user.click(screen.getByRole('button', { name: 'Send' }))
  }
}

describe('LiveDialogue', () => {
  it('starts in INTRO without exposing future partner turns', () => {
    render(<LiveDialogue lesson={lesson} provider={new LocalMockDialogueProvider()} onComplete={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Live Dialogue', level: 2 })).toBeInTheDocument()
    expect(screen.getByText(/A new conversation about/)).toBeInTheDocument()
    expect(screen.queryByText('What detail would you add, and why does it matter to you?')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Realtime Voice Practice' })).not.toBeInTheDocument()
  })

  it('starts a session, prevents empty sends, and renders turns in order', async () => {
    const user = userEvent.setup()
    render(<LiveDialogue lesson={lesson} provider={new LocalMockDialogueProvider()} onComplete={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Start Conversation' }))
    expect(await screen.findByText(/What stands out to you/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    await user.type(screen.getByLabelText('Your response'), 'I enjoy making plans.')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('I enjoy making plans.')).toBeInTheDocument()
    expect(await screen.findByText('What detail would you add, and why does it matter to you?')).toBeInTheDocument()
  })

  it('prevents duplicate submissions while the provider response is pending', async () => {
    const pendingTurn = deferred<ProviderTurn>()
    const provider: LiveDialogueProvider = {
      startSession: vi.fn(async (): Promise<ProviderTurn> => ({
        message: { id: 'partner-0', role: 'partner', text: 'First prompt' },
        completed: false,
        nextTurnIndex: 0,
        totalLearnerTurns: 3,
      })),
      sendLearnerTurn: vi.fn(() => pendingTurn.promise),
    }
    const user = userEvent.setup()
    render(<LiveDialogue lesson={lesson} provider={provider} onComplete={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Start Conversation' }))
    await user.type(screen.getByLabelText('Your response'), 'One response')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByRole('button', { name: 'Sending...' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Sending...' }))
    expect(provider.sendLearnerTurn).toHaveBeenCalledTimes(1)

    await act(async () => pendingTurn.resolve({
      message: { id: 'partner-1', role: 'partner', text: 'Next prompt' },
      completed: false,
      nextTurnIndex: 1,
      totalLearnerTurns: 3,
    }))
    expect(await screen.findByText('Next prompt')).toBeInTheDocument()
  })

  it('shows review, learner-only chunk reuse, and completes without gating on self-review', async () => {
    const onComplete = vi.fn()
    const user = userEvent.setup()
    render(<LiveDialogue lesson={lesson} provider={new LocalMockDialogueProvider()} onComplete={onComplete} />)

    await finishConversation(user)

    expect(await screen.findByRole('heading', { name: 'Conversation review' })).toBeInTheDocument()
    expect(screen.getByText('Useful expressions reused: 2 / 2')).toBeInTheDocument()
    expect(screen.getByText(/The app does not verify them\./)).toBeInTheDocument()
    expect(screen.getByText('Would you like to meet this weekend?')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Complete Live Dialogue' }))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('renders provider errors and can restart without old transcript state', async () => {
    let startAttempts = 0
    const provider: LiveDialogueProvider = {
      startSession: vi.fn(async (): Promise<ProviderTurn> => {
        startAttempts += 1
        if (startAttempts === 1) {
          throw new Error('Local provider unavailable')
        }
        return {
          message: { id: 'partner-0', role: 'partner', text: 'Recovered prompt' },
          completed: false,
          nextTurnIndex: 0,
          totalLearnerTurns: 3,
        }
      }),
      sendLearnerTurn: vi.fn(),
    }
    const user = userEvent.setup()
    render(<LiveDialogue lesson={lesson} provider={provider} onComplete={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Start Conversation' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Local provider unavailable')
    await user.click(screen.getByRole('button', { name: 'Try Again' }))
    expect(await screen.findByText('Recovered prompt')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Restart Dialogue' }))
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toBeInTheDocument()
    expect(screen.queryByText('Recovered prompt')).not.toBeInTheDocument()
  })

  it('ignores a pending provider result after unmount', async () => {
    const pendingStart = deferred<ProviderTurn>()
    const provider: LiveDialogueProvider = {
      startSession: vi.fn((_context: LiveDialogueProviderContext) => pendingStart.promise),
      sendLearnerTurn: vi.fn(),
    }
    const user = userEvent.setup()
    const view = render(<LiveDialogue lesson={lesson} provider={provider} onComplete={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Start Conversation' }))
    view.unmount()

    await act(async () => pendingStart.resolve({
      message: { id: 'late', role: 'partner', text: 'Late response' },
      completed: false,
      nextTurnIndex: 0,
      totalLearnerTurns: 3,
    }))
    expect(screen.queryByText('Late response')).not.toBeInTheDocument()
  })

  it('keeps local mode as default and connects voice only after explicit start', async () => {
    const voiceProvider = new FakeVoiceProvider()
    const user = userEvent.setup()
    render(
      <LiveDialogue
        lesson={lesson}
        provider={new LocalMockDialogueProvider()}
        voiceProvider={voiceProvider}
        onComplete={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Local Text Practice' })).toHaveAttribute('aria-pressed', 'true')
    expect(voiceProvider.connect).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Realtime Voice Practice' }))
    expect(voiceProvider.connect).not.toHaveBeenCalled()
    expect(screen.getByText('Realtime voice uses the configured AI API.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Start Voice Conversation' }))
    expect(voiceProvider.connect).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('Listening')).toBeInTheDocument()
  })

  it('maps voice transcripts, deduplicates events, handles interruption, and reuses the shared review', async () => {
    const voiceProvider = new FakeVoiceProvider()
    const user = userEvent.setup()
    render(
      <LiveDialogue
        lesson={lesson}
        provider={new LocalMockDialogueProvider()}
        voiceProvider={voiceProvider}
        onComplete={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Realtime Voice Practice' }))
    await user.click(screen.getByRole('button', { name: 'Start Voice Conversation' }))

    act(() => {
      voiceProvider.emitTranscript({ id: 'learner-1', role: 'learner', text: 'Would you like to meet?' })
      voiceProvider.emitTranscript({ id: 'learner-1', role: 'learner', text: 'Would you like to meet?' })
      voiceProvider.emitTranscript({ id: 'partner-1', role: 'partner', text: 'This weekend sounds good.' })
      voiceProvider.emitInterrupted()
    })

    expect(screen.getAllByText('Would you like to meet?')).toHaveLength(1)
    expect(screen.getByText('The partner was interrupted and is listening to you.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'End Conversation' }))

    expect(await screen.findByRole('heading', { name: 'Conversation review' })).toBeInTheDocument()
    expect(screen.getByText('Useful expressions reused: 1 / 2')).toBeInTheDocument()
    expect(screen.getByText('This weekend sounds good.')).toBeInTheDocument()
  })

  it('offers Local Text Practice after a realtime connection failure', async () => {
    const voiceProvider = new FakeVoiceProvider()
    voiceProvider.connect.mockRejectedValueOnce(new Error('Credential endpoint unavailable'))
    const user = userEvent.setup()
    render(
      <LiveDialogue
        lesson={lesson}
        provider={new LocalMockDialogueProvider()}
        voiceProvider={voiceProvider}
        onComplete={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Realtime Voice Practice' }))
    await user.click(screen.getByRole('button', { name: 'Start Voice Conversation' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Credential endpoint unavailable')
    await user.click(screen.getByRole('button', { name: 'Use Local Text Practice' }))
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toBeInTheDocument()
  })

  it('disconnects an active voice provider when the stage unmounts', async () => {
    const voiceProvider = new FakeVoiceProvider()
    const user = userEvent.setup()
    const view = render(
      <LiveDialogue
        lesson={lesson}
        provider={new LocalMockDialogueProvider()}
        voiceProvider={voiceProvider}
        onComplete={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Realtime Voice Practice' }))
    await user.click(screen.getByRole('button', { name: 'Start Voice Conversation' }))
    view.unmount()

    expect(voiceProvider.disconnect).toHaveBeenCalledWith('stage_exit')
  })
})
