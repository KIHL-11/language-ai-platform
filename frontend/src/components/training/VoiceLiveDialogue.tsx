import { useEffect, useRef, useState } from 'react'

import {
  upsertDialogueMessage,
  type DialogueChunk,
  type DialogueMessage,
  type DialogueScenario,
} from '../../training/liveDialogue'
import { buildLiveDialoguePrompt } from '../../training/liveDialoguePrompt'
import {
  initialVoiceDialogueStatus,
  type VoiceEndReason,
  type VoiceLiveDialogueProvider,
} from '../../training/voiceLiveDialogueProvider'
import type { Lesson } from '../../types/lesson'
import DialogueReview from './DialogueReview'

interface VoiceLiveDialogueProps {
  lesson: Lesson
  scenario: DialogueScenario
  candidateChunks: DialogueChunk[]
  provider: VoiceLiveDialogueProvider
  onChooseLocal: () => void
  onComplete: () => void
}

type VoicePhase = 'intro' | 'connecting' | 'conversation' | 'review' | 'error'

const END_MESSAGES: Partial<Record<VoiceEndReason, string>> = {
  turn_limit: 'The conversation ended after the five-turn practice limit.',
  time_limit: 'The conversation ended at the three-minute practice limit.',
  user: 'You ended the voice conversation.',
}

function friendlyError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return 'Microphone permission was denied. Allow microphone access or return to Local Text Practice.'
  }
  if (error instanceof DOMException && error.name === 'NotFoundError') {
    return 'No microphone is available. Connect a microphone or return to Local Text Practice.'
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return 'The realtime voice conversation could not be started.'
}

function statusLabel(value: string): string {
  return value.replace('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

export default function VoiceLiveDialogue({
  lesson,
  scenario,
  candidateChunks,
  provider,
  onChooseLocal,
  onComplete,
}: VoiceLiveDialogueProps) {
  const [phase, setPhase] = useState<VoicePhase>('intro')
  const [messages, setMessages] = useState<DialogueMessage[]>([])
  const [voiceStatus, setVoiceStatus] = useState(initialVoiceDialogueStatus)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    const unsubscribes = [
      provider.onTranscript((event) => {
        setMessages((current) => upsertDialogueMessage(current, event))
      }),
      provider.onStatusChange(setVoiceStatus),
      provider.onInterrupted(() => {
        setNotice('The partner was interrupted and is listening to you.')
      }),
      provider.onNotice(setNotice),
      provider.onError((providerError) => {
        setError(friendlyError(providerError))
        setPhase('error')
        started.current = false
      }),
      provider.onEnded((reason) => {
        setNotice(END_MESSAGES[reason] ?? null)
        setPhase('review')
        started.current = false
      }),
    ]

    return () => {
      unsubscribes.forEach((unsubscribe) => unsubscribe())
      if (started.current) {
        void provider.disconnect('stage_exit')
        started.current = false
      }
    }
  }, [provider])

  async function startVoiceConversation() {
    if (started.current) {
      return
    }
    started.current = true
    setPhase('connecting')
    setError(null)
    setNotice(null)
    setMessages([])

    try {
      await provider.connect({
        lesson,
        scenario,
        candidateChunks,
        instructions: buildLiveDialoguePrompt(lesson, scenario, candidateChunks),
      })
      if (started.current) {
        setPhase('conversation')
      }
    } catch (caught) {
      started.current = false
      setError(friendlyError(caught))
      setPhase('error')
    }
  }

  async function endConversation() {
    if (!started.current) {
      return
    }
    await provider.disconnect('user')
  }

  function restartVoice() {
    setMessages([])
    setNotice(null)
    setError(null)
    setPhase('intro')
  }

  if (phase === 'review') {
    return (
      <DialogueReview
        messages={messages}
        candidateChunks={candidateChunks}
        notice={notice}
        onRestart={restartVoice}
        onComplete={onComplete}
      />
    )
  }

  if (phase === 'error') {
    return (
      <div className="border-y border-[#c9d5d0] bg-white px-5 py-7 sm:px-6">
        <h3 className="text-lg font-semibold text-[#25312d]">Realtime voice unavailable</h3>
        <p role="alert" className="mt-3 max-w-2xl text-sm leading-6 text-[#9d241b]">{error}</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={onChooseLocal}
            className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548]"
          >
            Use Local Text Practice
          </button>
          <button
            type="button"
            onClick={restartVoice}
            className="h-10 border border-[#aebcb6] bg-white px-5 text-sm font-semibold text-[#34413d] hover:bg-[#f3f7f5]"
          >
            Return to Voice Intro
          </button>
        </div>
      </div>
    )
  }

  if (phase === 'intro') {
    return (
      <div>
        <div className="border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6">
          <p className="text-xs font-semibold uppercase text-[#176b5b]">Realtime scenario</p>
          <h3 className="mt-2 text-xl font-semibold text-[#25312d]">{scenario.title}</h3>
          <p className="mt-3 max-w-3xl leading-7 text-[#596560]">{scenario.description}</p>
          <p className="mt-5 text-sm font-semibold text-[#34413d]">Useful expressions</p>
          {candidateChunks.length > 0 ? (
            <ul className="mt-2 space-y-1 text-sm text-[#596560]">
              {candidateChunks.map((chunk) => <li key={chunk.id}>{chunk.text}</li>)}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-[#7a8581]">Respond naturally in your own words.</p>
          )}
        </div>
        <p className="mt-4 text-sm text-[#7a5b18]">
          Realtime voice uses the configured AI API.
        </p>
        <div className="mt-6 flex justify-end">
          <button
            type="button"
            onClick={() => void startVoiceConversation()}
            className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
          >
            Start Voice Conversation
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="grid gap-px border-y border-[#c9d5d0] bg-[#c9d5d0] sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Connection', voiceStatus.connection],
          ['Microphone', voiceStatus.microphone],
          ['Partner', voiceStatus.partner],
          ['Conversation', voiceStatus.conversation],
        ].map(([label, value]) => (
          <div key={label} className="bg-white px-5 py-4">
            <p className="text-xs text-[#66726e]">{label}</p>
            <p className="mt-1 text-sm font-semibold text-[#25312d]">{statusLabel(value)}</p>
          </div>
        ))}
      </div>

      {phase === 'connecting' ? (
        <p role="status" className="mt-5 text-sm text-[#596560]">
          Connecting securely and requesting microphone access...
        </p>
      ) : (
        <div>
          {notice && <p role="status" className="mt-5 text-sm text-[#596560]">{notice}</p>}
          <ol aria-label="Voice conversation transcript" className="mt-5 space-y-4">
            {messages.map((message) => (
              <li
                key={message.id}
                className={message.role === 'learner' ? 'ml-auto max-w-2xl border-r-4 border-[#176b5b] bg-[#edf6f2] px-5 py-4 text-right' : 'mr-auto max-w-2xl border-l-4 border-[#d19a2a] bg-white px-5 py-4'}
              >
                <p className="text-xs font-semibold uppercase text-[#66726e]">
                  {message.role === 'learner' ? 'You' : 'Partner'}
                </p>
                <p className="mt-1 leading-7 text-[#25312d]">{message.text}</p>
              </li>
            ))}
          </ol>
          {messages.length === 0 && (
            <p className="mt-5 text-sm text-[#66726e]">Waiting for the first spoken turn...</p>
          )}
        </div>
      )}

      <div className="mt-6 flex flex-wrap justify-end gap-3">
        <button
          type="button"
          onClick={() => provider.mute(voiceStatus.microphone !== 'muted')}
          disabled={phase !== 'conversation'}
          className="h-10 border border-[#aebcb6] bg-white px-5 text-sm font-semibold text-[#34413d] hover:bg-[#f3f7f5] disabled:cursor-not-allowed disabled:text-[#a0aaa6]"
        >
          {voiceStatus.microphone === 'muted' ? 'Unmute' : 'Mute'}
        </button>
        <button
          type="button"
          onClick={() => void endConversation()}
          disabled={phase !== 'conversation'}
          className="h-10 bg-[#9d241b] px-5 text-sm font-semibold text-white hover:bg-[#7e1d16] disabled:cursor-not-allowed disabled:bg-[#b9a09d]"
        >
          End Conversation
        </button>
      </div>
    </div>
  )
}
