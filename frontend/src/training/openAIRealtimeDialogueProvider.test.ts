import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RealtimeItem } from '@openai/agents/realtime'

import type { Lesson } from '../types/lesson'
import {
  OpenAIRealtimeDialogueProvider,
  transcriptFromRealtimeItem,
  type RealtimeSessionAdapter,
} from './openAIRealtimeDialogueProvider'
import type { VoiceDialogueContext, VoiceEndReason } from './voiceLiveDialogueProvider'

const context: VoiceDialogueContext = {
  lesson: {
    id: 'voice-provider',
    title: 'Me at the zoo',
    source_language: 'en',
    level: 'B2',
  } as Lesson,
  scenario: {
    id: 'scenario',
    title: 'Describe a new place',
    description: 'Tell a friend about a place.',
    learnerGoal: 'Share one detail.',
  },
  candidateChunks: [],
  instructions: 'Speak in English.',
}

function messageItem(
  id: string,
  role: 'user' | 'assistant',
  transcript: string,
): RealtimeItem {
  if (role === 'user') {
    return {
      itemId: id,
      type: 'message',
      role: 'user',
      status: 'completed',
      content: [{ type: 'input_audio', audio: null, transcript }],
    }
  }
  return {
    itemId: id,
    type: 'message',
    role: 'assistant',
    status: 'completed',
    content: [{ type: 'output_audio', audio: null, transcript }],
  }
}

function setupProvider() {
  let callbacks: Parameters<RealtimeSessionAdapter['subscribe']>[0] | null = null
  const adapter: RealtimeSessionAdapter = {
    connect: vi.fn().mockResolvedValue(undefined),
    requestInitialResponse: vi.fn(),
    mute: vi.fn(),
    close: vi.fn(),
    subscribe: vi.fn((nextCallbacks) => {
      callbacks = nextCallbacks
      return vi.fn()
    }),
  }
  const stop = vi.fn()
  const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream
  const getUserMedia = vi.fn().mockResolvedValue(stream)
  const fetchCredential = vi.fn().mockResolvedValue({
    value: 'ek_test_only',
    expires_at: 100,
    model: 'gpt-realtime-2.1-mini',
    voice: 'marin',
  })
  const provider = new OpenAIRealtimeDialogueProvider({
    fetchCredential,
    getUserMedia,
    createSession: vi.fn(() => adapter),
  })
  return {
    provider,
    adapter,
    stop,
    getUserMedia,
    fetchCredential,
    callbacks: () => {
      if (!callbacks) {
        throw new Error('Session callbacks are not ready.')
      }
      return callbacks
    },
  }
}

describe('OpenAIRealtimeDialogueProvider', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('does not request credentials or microphone before connect', () => {
    const fixture = setupProvider()
    expect(fixture.fetchCredential).not.toHaveBeenCalled()
    expect(fixture.getUserMedia).not.toHaveBeenCalled()
  })

  it('connects only on demand and maps completed transcripts', async () => {
    const fixture = setupProvider()
    const transcripts = vi.fn()
    fixture.provider.onTranscript(transcripts)

    await fixture.provider.connect(context)
    fixture.callbacks().onHistory([
      messageItem('learner-1', 'user', 'I am in front of the zoo.'),
      messageItem('partner-1', 'assistant', 'What did you see?'),
    ])

    expect(fixture.fetchCredential).toHaveBeenCalledTimes(1)
    expect(fixture.getUserMedia).toHaveBeenCalledTimes(1)
    expect(fixture.adapter.connect).toHaveBeenCalledWith('ek_test_only')
    expect(fixture.adapter.requestInitialResponse).toHaveBeenCalledTimes(1)
    expect(transcripts).toHaveBeenCalledWith({
      id: 'learner-1',
      role: 'learner',
      text: 'I am in front of the zoo.',
    })
    expect(transcripts).toHaveBeenCalledWith({
      id: 'partner-1',
      role: 'partner',
      text: 'What did you see?',
    })
  })

  it('deduplicates history events and reports interruption', async () => {
    const fixture = setupProvider()
    const transcripts = vi.fn()
    const interrupted = vi.fn()
    fixture.provider.onTranscript(transcripts)
    fixture.provider.onInterrupted(interrupted)
    await fixture.provider.connect(context)

    const item = messageItem('learner-1', 'user', 'One turn')
    fixture.callbacks().onHistory([item])
    fixture.callbacks().onHistory([item])
    fixture.callbacks().onInterrupted()

    expect(transcripts).toHaveBeenCalledTimes(1)
    expect(interrupted).toHaveBeenCalledTimes(1)
  })

  it('ends after five unique learner turns and the following partner reply', async () => {
    const fixture = setupProvider()
    const ended: VoiceEndReason[] = []
    fixture.provider.onEnded((reason) => ended.push(reason))
    await fixture.provider.connect(context)

    for (let index = 1; index <= 5; index += 1) {
      fixture.callbacks().onHistory([messageItem(`learner-${index}`, 'user', `Turn ${index}`)])
    }
    expect(fixture.adapter.close).not.toHaveBeenCalled()
    fixture.callbacks().onHistory([messageItem('partner-final', 'assistant', 'Thanks for talking.')])
    await Promise.resolve()

    expect(fixture.adapter.close).toHaveBeenCalledTimes(1)
    expect(fixture.stop).toHaveBeenCalledTimes(1)
    expect(ended).toEqual(['turn_limit'])
  })

  it('enforces the three-minute limit and releases resources', async () => {
    const fixture = setupProvider()
    const ended: VoiceEndReason[] = []
    fixture.provider.onEnded((reason) => ended.push(reason))
    await fixture.provider.connect(context)

    await vi.advanceTimersByTimeAsync(3 * 60 * 1000)

    expect(fixture.adapter.close).toHaveBeenCalledTimes(1)
    expect(fixture.stop).toHaveBeenCalledTimes(1)
    expect(ended).toEqual(['time_limit'])
  })

  it('disconnects explicitly and stops microphone tracks', async () => {
    const fixture = setupProvider()
    await fixture.provider.connect(context)
    await fixture.provider.disconnect('stage_exit')

    expect(fixture.adapter.close).toHaveBeenCalledTimes(1)
    expect(fixture.stop).toHaveBeenCalledTimes(1)
  })
})

describe('transcriptFromRealtimeItem', () => {
  it('ignores unavailable and in-progress transcripts', () => {
    const inProgress = {
      ...messageItem('pending', 'user', ''),
      status: 'in_progress',
    } as RealtimeItem
    expect(transcriptFromRealtimeItem(inProgress)).toBeNull()
    expect(transcriptFromRealtimeItem(messageItem('empty', 'user', ''))).toBeNull()
  })
})
