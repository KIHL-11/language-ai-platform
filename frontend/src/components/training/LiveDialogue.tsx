import { useEffect, useMemo, useRef, useState } from 'react'

import {
  generateDialogueScenario,
  selectDialogueChunks,
  type DialogueMessage,
  type DialogueSessionStatus,
} from '../../training/liveDialogue'
import type {
  LiveDialogueProvider,
  LiveDialogueProviderContext,
} from '../../training/liveDialogueProvider'
import type { VoiceLiveDialogueProvider } from '../../training/voiceLiveDialogueProvider'
import type { Lesson } from '../../types/lesson'
import DialogueReview from './DialogueReview'
import VoiceLiveDialogue from './VoiceLiveDialogue'

interface LiveDialogueProps {
  lesson: Lesson
  provider: LiveDialogueProvider
  voiceProvider?: VoiceLiveDialogueProvider
  onComplete: () => void
}

function errorMessage(error: unknown, action: string): string {
  const detail = error instanceof Error && error.message.trim()
    ? ` ${error.message}`
    : ''
  return `${action} Please try again.${detail}`
}

export default function LiveDialogue({
  lesson,
  provider,
  voiceProvider,
  onComplete,
}: LiveDialogueProps) {
  const scenario = useMemo(() => generateDialogueScenario(lesson), [lesson])
  const candidateChunks = useMemo(() => selectDialogueChunks(lesson), [lesson])
  const [status, setStatus] = useState<DialogueSessionStatus>('intro')
  const [messages, setMessages] = useState<DialogueMessage[]>([])
  const [turnIndex, setTurnIndex] = useState(0)
  const [totalLearnerTurns, setTotalLearnerTurns] = useState(0)
  const [learnerText, setLearnerText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'local' | 'voice'>('local')
  const requestVersion = useRef(0)
  const pendingRef = useRef(false)

  useEffect(() => () => {
    requestVersion.current += 1
    pendingRef.current = false
  }, [])

  function providerContext(
    nextMessages = messages,
    nextTurnIndex = turnIndex,
  ): LiveDialogueProviderContext {
    return {
      lesson,
      scenario,
      candidateChunks,
      messages: nextMessages,
      turnIndex: nextTurnIndex,
    }
  }

  function resetDialogue() {
    requestVersion.current += 1
    pendingRef.current = false
    setStatus('intro')
    setMessages([])
    setTurnIndex(0)
    setTotalLearnerTurns(0)
    setLearnerText('')
    setPending(false)
    setError(null)
  }

  async function startConversation() {
    if (pendingRef.current) {
      return
    }
    pendingRef.current = true
    setPending(true)
    setError(null)
    setStatus('starting')
    const version = ++requestVersion.current

    try {
      const turn = await provider.startSession(providerContext([], 0))
      if (requestVersion.current !== version) {
        return
      }
      setMessages([turn.message])
      setTurnIndex(turn.nextTurnIndex)
      setTotalLearnerTurns(turn.totalLearnerTurns)
      setStatus(turn.completed ? 'review' : 'conversation')
    } catch (caught) {
      if (requestVersion.current !== version) {
        return
      }
      setStatus('error')
      setError(errorMessage(caught, 'The conversation could not be started.'))
    } finally {
      if (requestVersion.current === version) {
        pendingRef.current = false
        setPending(false)
      }
    }
  }

  async function sendLearnerTurn() {
    const submittedText = learnerText.trim()
    if (!submittedText || pendingRef.current || status !== 'conversation') {
      return
    }

    const learnerMessage: DialogueMessage = {
      id: `learner-${turnIndex + 1}`,
      role: 'learner',
      text: submittedText,
    }
    const nextMessages = [...messages, learnerMessage]
    pendingRef.current = true
    setPending(true)
    setError(null)
    const version = ++requestVersion.current

    try {
      const turn = await provider.sendLearnerTurn(
        providerContext(nextMessages, turnIndex),
        submittedText,
      )
      if (requestVersion.current !== version) {
        return
      }
      setMessages([...nextMessages, turn.message])
      setTurnIndex(turn.nextTurnIndex)
      setTotalLearnerTurns(turn.totalLearnerTurns)
      setLearnerText('')
      setStatus(turn.completed ? 'review' : 'conversation')
    } catch (caught) {
      if (requestVersion.current !== version) {
        return
      }
      setError(errorMessage(caught, 'The partner response failed.'))
    } finally {
      if (requestVersion.current === version) {
        pendingRef.current = false
        setPending(false)
      }
    }
  }

  function completeDialogue() {
    if (status !== 'review') {
      return
    }
    setStatus('complete')
    onComplete()
  }

  function modeControl() {
    if (!voiceProvider) {
      return null
    }
    return (
      <div className="mb-6 inline-flex border border-[#aebcb6] bg-white p-1" aria-label="Live Dialogue mode">
        <button
          type="button"
          aria-pressed={mode === 'local'}
          onClick={() => setMode('local')}
          className={`h-9 px-4 text-sm font-semibold ${mode === 'local' ? 'bg-[#176b5b] text-white' : 'text-[#34413d] hover:bg-[#f3f7f5]'}`}
        >
          Local Text Practice
        </button>
        <button
          type="button"
          aria-pressed={mode === 'voice'}
          onClick={() => setMode('voice')}
          className={`h-9 px-4 text-sm font-semibold ${mode === 'voice' ? 'bg-[#176b5b] text-white' : 'text-[#34413d] hover:bg-[#f3f7f5]'}`}
        >
          Realtime Voice Practice
        </button>
      </div>
    )
  }

  if (mode === 'voice' && voiceProvider) {
    return (
      <section aria-labelledby="live-dialogue-title">
        <div className="mb-6">
          <h2 id="live-dialogue-title" className="text-2xl font-semibold text-[#18211f]">
            Live Dialogue
          </h2>
          <p className="mt-2 max-w-2xl leading-7 text-[#596560]">
            Transfer lesson language into a new conversation.
          </p>
        </div>
        {modeControl()}
        <VoiceLiveDialogue
          lesson={lesson}
          scenario={scenario}
          candidateChunks={candidateChunks}
          provider={voiceProvider}
          onChooseLocal={() => setMode('local')}
          onComplete={onComplete}
        />
      </section>
    )
  }

  return (
    <section aria-labelledby="live-dialogue-title">
      <div className="mb-6">
        <h2 id="live-dialogue-title" className="text-2xl font-semibold text-[#18211f]">
          Live Dialogue
        </h2>
        <p className="mt-2 max-w-2xl leading-7 text-[#596560]">
          Transfer lesson language into a new conversation.
        </p>
      </div>
      {modeControl()}

      {(status === 'intro' || status === 'starting' || status === 'error') && (
        <div>
          <div className="border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6">
            <p className="text-xs font-semibold uppercase text-[#176b5b]">Scenario</p>
            <h3 className="mt-2 text-xl font-semibold text-[#25312d]">{scenario.title}</h3>
            <p className="mt-3 max-w-3xl leading-7 text-[#596560]">{scenario.description}</p>
            <div className="mt-5 border-l-4 border-[#d19a2a] pl-4">
              <p className="text-sm font-semibold text-[#34413d]">Your goal</p>
              <p className="mt-1 text-sm leading-6 text-[#596560]">{scenario.learnerGoal}</p>
            </div>
          </div>

          <div className="mt-5 border-y border-[#c9d5d0] bg-[#f8faf9] px-5 py-5 sm:px-6">
            <h3 className="font-semibold text-[#25312d]">Useful expressions you may reuse</h3>
            {candidateChunks.length > 0 ? (
              <ul className="mt-3 space-y-2 text-sm text-[#596560]">
                {candidateChunks.map((chunk) => (
                  <li key={chunk.id}>
                    <span className="font-medium text-[#25312d]">{chunk.text}</span>
                    {chunk.meaning ? `: ${chunk.meaning}` : ''}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-[#7a8581]">
                No lesson chunks are available. Respond in your own words.
              </p>
            )}
          </div>

          {error && <p role="alert" className="mt-4 text-sm text-[#9d241b]">{error}</p>}
          <div className="mt-6 flex justify-end">
            <button
              type="button"
              onClick={() => void startConversation()}
              disabled={pending}
              className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-wait disabled:bg-[#9aaba4]"
            >
              {pending ? 'Starting...' : status === 'error' ? 'Try Again' : 'Start Conversation'}
            </button>
          </div>
        </div>
      )}

      {status === 'conversation' && (
        <div>
          <div className="flex items-center justify-between border-b border-[#c9d5d0] pb-3 text-sm text-[#66726e]">
            <span>Conversation</span>
            <span className="tabular-nums">
              Turn {Math.min(turnIndex + 1, totalLearnerTurns)} of {totalLearnerTurns}
            </span>
          </div>

          <ol aria-label="Conversation transcript" className="mt-5 space-y-4">
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

          <div className="mt-6 border-y border-[#c9d5d0] bg-white px-5 py-5 sm:px-6">
            <label htmlFor="dialogue-response" className="text-sm font-semibold text-[#34413d]">
              Your response
            </label>
            <textarea
              id="dialogue-response"
              value={learnerText}
              onChange={(event) => setLearnerText(event.target.value)}
              disabled={pending}
              rows={3}
              className="mt-2 w-full resize-y border border-[#aebcb6] bg-white px-3 py-2 text-[#18211f] outline-none focus:border-[#176b5b] focus:ring-2 focus:ring-[#176b5b]/20 disabled:bg-[#f1f4f2]"
            />
            {error && <p role="alert" className="mt-3 text-sm text-[#9d241b]">{error}</p>}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                onClick={resetDialogue}
                className="h-10 border border-[#aebcb6] bg-white px-4 text-sm font-semibold text-[#34413d] hover:bg-[#f3f7f5]"
              >
                Restart Dialogue
              </button>
              <button
                type="button"
                onClick={() => void sendLearnerTurn()}
                disabled={!learnerText.trim() || pending}
                className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-[#9aaba4]"
              >
                {pending ? 'Sending...' : 'Send'}
              </button>
            </div>
            {pending && <p role="status" className="mt-3 text-sm text-[#66726e]">Partner is responding...</p>}
          </div>
        </div>
      )}

      {status === 'review' && (
        <DialogueReview
          messages={messages}
          candidateChunks={candidateChunks}
          onRestart={resetDialogue}
          onComplete={completeDialogue}
        />
      )}
    </section>
  )
}
