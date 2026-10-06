import { useEffect, useRef, useState } from 'react'

import ActiveRecall from '../components/training/ActiveRecall'
import BlindListening from '../components/training/BlindListening'
import Comprehension from '../components/training/Comprehension'
import LiveDialogue from '../components/training/LiveDialogue'
import MiniDictation from '../components/training/MiniDictation'
import Retell from '../components/training/Retell'
import Shadowing from '../components/training/Shadowing'
import TrainingFlow from '../components/TrainingFlow'
import {
  DEV_TRAINING_STAGES,
  getDevStageOverride,
  updateDevStageQuery,
} from '../training/devStageJump'
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
  devMode?: boolean
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
  devMode = true,
}: TrainingSessionProps) {
  const isDevelopment = import.meta.env.DEV && devMode
  const initialDevStage = getDevStageOverride(
    window.location.search,
    isDevelopment,
  )
  const progressionLock = useRef(false)
  const [devOverride, setDevOverride] = useState<{
    stage: TrainingStage
    sentenceIndex: number
    revision: number
  } | null>(() =>
    initialDevStage
      ? { stage: initialDevStage, sentenceIndex: 0, revision: 0 }
      : null,
  )
  const [configuredVoiceProvider] = useState(
    createConfiguredRealtimeDialogueProvider,
  )
  const {
    state,
    currentStage: sequentialStage,
    nextStage: nextSequentialStage,
    previousStage: previousSequentialStage,
    nextSentence: nextSequentialSentence,
    previousSentence: previousSequentialSentence,
    restartStage: restartSequentialStage,
    restartSession: restartSequentialSession,
  } = useTrainingSession(lesson.training_order, lesson.sentences.length)

  const currentStage = devOverride?.stage ?? sequentialStage
  const sentenceIndex = devOverride?.sentenceIndex ?? state.sentenceIndex
  const currentSentence = lesson.sentences[sentenceIndex]
  const isSentenceStage = SENTENCE_STAGES.has(currentStage)
  const isFinalSentence = sentenceIndex === lesson.sentences.length - 1
  const isLastStage = state.stageIndex === state.order.length - 1
  const isSessionComplete =
    !devOverride && isLastStage && state.completedStages.includes(currentStage)
  const stageLabel = TRAINING_STAGE_LABELS[currentStage]
  const displayedStageIndex = devOverride
    ? DEV_TRAINING_STAGES.indexOf(currentStage)
    : state.stageIndex
  const displayedStageCount = devOverride
    ? DEV_TRAINING_STAGES.length
    : state.order.length
  const stageProgress =
    ((displayedStageIndex + 1) / displayedStageCount) * 100

  useEffect(() => {
    progressionLock.current = false
  }, [currentStage, sentenceIndex])

  function setDevelopmentStage(stage: TrainingStage) {
    progressionLock.current = false
    setDevOverride((current) => ({
      stage,
      sentenceIndex: 0,
      revision: (current?.revision ?? 0) + 1,
    }))
    updateDevStageQuery(stage)
  }

  function advanceOneStage() {
    if (progressionLock.current) {
      return
    }
    progressionLock.current = true
    if (devOverride) {
      const nextIndex = DEV_TRAINING_STAGES.indexOf(devOverride.stage) + 1
      if (nextIndex < DEV_TRAINING_STAGES.length) {
        setDevelopmentStage(DEV_TRAINING_STAGES[nextIndex])
      }
      return
    }
    nextSequentialStage()
  }

  function handleSentenceAdvance() {
    if (progressionLock.current) {
      return
    }
    progressionLock.current = true
    if (devOverride) {
      if (isFinalSentence) {
        const nextIndex = DEV_TRAINING_STAGES.indexOf(devOverride.stage) + 1
        if (nextIndex < DEV_TRAINING_STAGES.length) {
          setDevelopmentStage(DEV_TRAINING_STAGES[nextIndex])
        }
      } else {
        setDevOverride((current) =>
          current
            ? { ...current, sentenceIndex: current.sentenceIndex + 1 }
            : current,
        )
      }
      return
    }
    nextSequentialSentence()
    if (isFinalSentence) {
      nextSequentialStage()
    }
  }

  function handlePreviousStage() {
    if (!devOverride) {
      previousSequentialStage()
      return
    }

    const previousIndex = DEV_TRAINING_STAGES.indexOf(devOverride.stage) - 1
    if (previousIndex >= 0) {
      setDevelopmentStage(DEV_TRAINING_STAGES[previousIndex])
    }
  }

  function handlePreviousSentence() {
    if (!devOverride) {
      previousSequentialSentence()
      return
    }
    setDevOverride((current) =>
      current
        ? { ...current, sentenceIndex: Math.max(0, current.sentenceIndex - 1) }
        : current,
    )
  }

  function handleRestartStage() {
    if (!devOverride) {
      restartSequentialStage()
      return
    }
    setDevOverride((current) =>
      current
        ? { ...current, sentenceIndex: 0, revision: current.revision + 1 }
        : current,
    )
  }

  function handleRestartSession() {
    setDevOverride(null)
    updateDevStageQuery(null)
    restartSequentialSession()
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
              onClick={handleRestartSession}
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
          key={`${currentStage}-${currentSentence.id}-${devOverride?.revision ?? 0}`}
          sentence={currentSentence}
          sentenceIndex={sentenceIndex}
          sentenceCount={lesson.sentences.length}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'blind_listening' && currentSentence) {
      return (
        <BlindListening
          key={`${currentStage}-${currentSentence.id}-${devOverride?.revision ?? 0}`}
          sentence={currentSentence}
          sentenceIndex={sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'comprehension' && currentSentence) {
      return (
        <Comprehension
          key={`${currentStage}-${currentSentence.id}-${devOverride?.revision ?? 0}`}
          sentence={currentSentence}
          lessonSentences={lesson.sentences}
          sentenceIndex={sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'mini_dictation' && currentSentence) {
      return (
        <MiniDictation
          key={`${currentStage}-${currentSentence.id}-${devOverride?.revision ?? 0}`}
          sentence={currentSentence}
          sentenceIndex={sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'shadowing' && currentSentence) {
      return (
        <Shadowing
          key={`${currentStage}-${currentSentence.id}-${devOverride?.revision ?? 0}`}
          sentence={currentSentence}
          sentenceIndex={sentenceIndex}
          sentenceCount={lesson.sentences.length}
          audioUrl={audioUrl}
          onNext={handleSentenceAdvance}
        />
      )
    }

    if (currentStage === 'retell') {
      return (
        <Retell
          key={`retell-${devOverride?.revision ?? 0}`}
          lesson={lesson}
          onComplete={advanceOneStage}
        />
      )
    }

    return (
      <LiveDialogue
        key={`live-dialogue-${devOverride?.revision ?? 0}`}
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
            Stage {displayedStageIndex + 1} of {displayedStageCount}
            {isSentenceStage && lesson.sentences.length > 0
              ? ` · Sentence ${sentenceIndex + 1} of ${lesson.sentences.length}`
              : ''}
          </p>
        </div>

        <div className="mt-5 h-1.5 bg-[#dfe7e3]" aria-label={`${Math.round(stageProgress)}% stage progress`}>
          <div className="h-full bg-[#176b5b]" style={{ width: `${stageProgress}%` }} />
        </div>
      </header>

      {isDevelopment && (
        <aside
          className="mt-6 border-2 border-[#b86b08] bg-[#fff4d6] px-4 py-4 text-[#5f3705]"
          aria-label="Development stage controls"
        >
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.16em]">
                DEV Stage Jump
              </p>
              <p className="mt-1 text-sm font-semibold" role="status">
                {devOverride
                  ? `DEV override: ${TRAINING_STAGE_LABELS[devOverride.stage]}`
                  : 'DEV stage override: None'}
              </p>
            </div>
            <p className="text-xs font-medium">Local development only</p>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {DEV_TRAINING_STAGES.map((stage) => (
              <button
                key={stage}
                type="button"
                aria-pressed={devOverride?.stage === stage}
                onClick={() => setDevelopmentStage(stage)}
                className="border border-[#b86b08] bg-white px-2.5 py-1.5 text-xs font-semibold text-[#5f3705] hover:bg-[#ffe7ae] aria-pressed:bg-[#b86b08] aria-pressed:text-white"
              >
                {TRAINING_STAGE_LABELS[stage]}
              </button>
            ))}
          </div>
        </aside>
      )}

      <div className="mt-8">
        <TrainingFlow
          stages={devOverride ? [...DEV_TRAINING_STAGES] : state.order}
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
              onClick={handlePreviousStage}
              disabled={
                devOverride
                  ? DEV_TRAINING_STAGES.indexOf(currentStage) === 0
                  : state.stageIndex === 0
              }
              className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] disabled:cursor-not-allowed disabled:text-[#a0aaa6]"
            >
              Previous stage
            </button>
            {isSentenceStage && (
              <button
                type="button"
                onClick={handlePreviousSentence}
                disabled={sentenceIndex === 0}
                className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] disabled:cursor-not-allowed disabled:text-[#a0aaa6]"
              >
                Previous sentence
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleRestartStage}
              className="h-9 border border-[#aebcb6] bg-white px-3 text-sm font-medium text-[#34413d] hover:bg-[#f3f7f5]"
            >
              Restart stage
            </button>
            <button
              type="button"
              onClick={handleRestartSession}
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
