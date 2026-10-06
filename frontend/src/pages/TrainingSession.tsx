import { useEffect, useRef, useState } from 'react'

import ActiveRecall from '../components/training/ActiveRecall'
import BlindListening from '../components/training/BlindListening'
import Comprehension from '../components/training/Comprehension'
import LiveDialogue from '../components/training/LiveDialogue'
import MiniDictation from '../components/training/MiniDictation'
import Retell from '../components/training/Retell'
import Shadowing from '../components/training/Shadowing'
import TrainingFlow from '../components/TrainingFlow'
import { TRAINING_STAGE_LABELS } from '../training/trainingMachine'
import {
  localMockDialogueProvider,
  type LiveDialogueProvider,
} from '../training/liveDialogueProvider'
import { createConfiguredRealtimeDialogueProvider } from '../training/openAIRealtimeDialogueProvider'
import type { VoiceLiveDialogueProvider } from '../training/voiceLiveDialogueProvider'
import { useTrainingSession } from '../training/useTrainingSession'
import type { Lesson, TrainingStage } from '../types/lesson'

interface TrainingSessionProps {
  lesson: Lesson
  audioUrl: string | null
  onExit: () => void
  liveDialogueProvider?: LiveDialogueProvider
  voiceLiveDialogueProvider?: VoiceLiveDialogueProvider
}

const SENTENCE_STAGES = new Set<TrainingStage>([
  'active_recall',
  'blind_listening',
  'comprehension',
  'mini_dictation',
  'shadowing',
])

export default function TrainingSession({
  lesson,
  audioUrl,
  onExit,
  liveDialogueProvider = localMockDialogueProvider,
  voiceLiveDialogueProvider,
}: TrainingSessionProps) {
  const progressionLock = useRef(false)
  const [configuredVoiceProvider] = useState(
    createConfiguredRealtimeDialogueProvider,
  )
  const {
    state,
    currentStage,
    nextStage,
    previousStage,
    nextSentence,
    previousSentence,
    restartStage,
    restartSession,
  } = useTrainingSession(lesson.training_order, lesson.sentences.length)

  const currentSentence = lesson.sentences[state.sentenceIndex]
  const isSentenceStage = SENTENCE_STAGES.has(currentStage)
  const isFinalSentence = state.sentenceIndex === lesson.sentences.length - 1
  const isLastStage = state.stageIndex === state.order.length - 1
  const isSessionComplete =
    isLastStage && state.completedStages.includes(currentStage)
  const stageLabel = TRAINING_STAGE_LABELS[currentStage]
  const stageProgress = ((state.stageIndex + 1) / state.order.length) * 100

  useEffect(() => {
    progressionLock.current = false
  }, [state.stageIndex, state.sentenceIndex])

  function advanceOneStage() {
    if (progressionLock.current) {
      return
    }
    progressionLock.current = true
    nextStage()
  }

  function handleSentenceAdvance() {
    if (progressionLock.current) {
      return
    }
    progressionLock.current = true
    nextSentence()
    if (isFinalSentence) {
      nextStage()
    }
  }

  function renderStage() {
    if (isSessionComplete) {
      return (
        <section className="border-y border-[#c9d5d0] bg-white px-5 py-10 sm:px-8" aria-labelledby="session-complete-title">
          <h2 id="session-complete-title" className="text-2xl font-semibold text-[#18211f]">
            Training Complete
          </h2>
          <p className="mt-3 text-[#596560]">
            Stages completed: {state.completedStages.length} / {state.order.length}
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={restartSession}
              className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
            >
              Restart Training
            </button>
            <button
              type="button"
              onClick={onExit}
              className="h-10 border border-[#aebcb6] bg-white px-5 text-sm font-semibold text-[#293430] hover:bg-[#f3f7f5] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
            >
              Review Lesson
            </button>
          </div>
        </section>
      )
    }

    if (isSentenceStage && !currentSentence) {
      return (
        <section className="border-y border-[#c9d5d0] bg-white px-5 py-8 sm:px-8">
          <p className="text-[#596560]">No sentences are available for this stage.</p>
          <button
            type="button"
            onClick={advanceOneStage}
            className="mt-5 h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white"
          >
            Continue to next stage
          </button>
        </section>
      )
    }

    if (currentStage === 'active_recall' && currentSentence) {
      return (
        <ActiveRecall
          key={currentSentence.id}
          sentence={currentSentence}
          sentenceIndex={state.sentenceIndex}
          sentenceCount={lesson.sentences.length}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'blind_listening' && currentSentence) {
      return (
        <BlindListening
          key={currentSentence.id}
          sentence={currentSentence}
          sentenceIndex={state.sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'comprehension' && currentSentence) {
      return (
        <Comprehension
          key={currentSentence.id}
          sentence={currentSentence}
          lessonSentences={lesson.sentences}
          sentenceIndex={state.sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'mini_dictation' && currentSentence) {
      return (
        <MiniDictation
          key={currentSentence.id}
          sentence={currentSentence}
          sentenceIndex={state.sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'shadowing' && currentSentence) {
      return (
        <Shadowing
          key={currentSentence.id}
          sentence={currentSentence}
          sentenceIndex={state.sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'retell') {
      return <Retell lesson={lesson} onComplete={advanceOneStage} />
    }

    return (
      <LiveDialogue
        lesson={lesson}
        provider={liveDialogueProvider}
        voiceProvider={voiceLiveDialogueProvider ?? configuredVoiceProvider}
        onComplete={advanceOneStage}
      />
    )
  }

  return (
    <article className="mt-10 border-t border-[#aebcb6] pt-8">
      <header>
        <button
          type="button"
          onClick={onExit}
          className="text-sm font-semibold text-[#176b5b] hover:text-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
        >
          Back to lesson
        </button>
        <div className="mt-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm text-[#66726e]">{lesson.title}</p>
            <h1 className="mt-1 text-3xl font-semibold text-[#18211f]">
              {stageLabel}
            </h1>
          </div>
          <p className="text-sm tabular-nums text-[#596560]">
            Stage {state.stageIndex + 1} of {state.order.length}
            {isSentenceStage && lesson.sentences.length > 0
              ? ` · Sentence ${state.sentenceIndex + 1} of ${lesson.sentences.length}`
              : ''}
          </p>
        </div>

        <div className="mt-5 h-1.5 bg-[#dfe7e3]" aria-label={`${Math.round(stageProgress)}% stage progress`}>
          <div className="h-full bg-[#176b5b]" style={{ width: `${stageProgress}%` }} />
        </div>
      </header>

      <div className="mt-8">
        <TrainingFlow
          stages={state.order}
          currentStage={currentStage}
          completedStages={state.completedStages}
        />
      </div>

      <div className="mt-9">{renderStage()}</div>

      {!isSessionComplete && (
        <nav className="mt-7 flex flex-col gap-4 border-t border-[#c9d5d0] pt-5 sm:flex-row sm:items-center sm:justify-between" aria-label="Training navigation">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={previousStage}
              disabled={state.stageIndex === 0}
              className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] disabled:cursor-not-allowed disabled:text-[#a0aaa6]"
            >
              Previous stage
            </button>
            {isSentenceStage && (
              <button
                type="button"
                onClick={previousSentence}
                disabled={state.sentenceIndex === 0}
                className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] disabled:cursor-not-allowed disabled:text-[#a0aaa6]"
              >
                Previous sentence
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={restartStage}
              className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] hover:bg-[#f3f7f5]"
            >
              Restart stage
            </button>
            <button
              type="button"
              onClick={restartSession}
              className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] hover:bg-[#f3f7f5]"
            >
              Restart session
            </button>
          </div>
        </nav>
      )}
    </article>
  )
}
