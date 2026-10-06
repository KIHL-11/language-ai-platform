import { useCallback, useState } from 'react'

import type { TrainingStage } from '../types/lesson'
import {
  createTrainingState,
  getCurrentStage,
  nextSentence as advanceSentence,
  nextStage as advanceStage,
  previousSentence as goToPreviousSentence,
  previousStage as goToPreviousStage,
  restartSession as resetSession,
  restartStage as resetStage,
} from './trainingMachine'

export function useTrainingSession(
  order: TrainingStage[],
  sentenceCount: number,
) {
  const [state, setState] = useState(() => createTrainingState(order))

  const nextStage = useCallback(() => {
    setState((current) => advanceStage(current))
  }, [])

  const previousStage = useCallback(() => {
    setState((current) => goToPreviousStage(current))
  }, [])

  const nextSentence = useCallback(() => {
    setState((current) => advanceSentence(current, sentenceCount))
  }, [sentenceCount])

  const previousSentence = useCallback(() => {
    setState((current) => goToPreviousSentence(current))
  }, [])

  const restartStage = useCallback(() => {
    setState((current) => resetStage(current))
  }, [])

  const restartSession = useCallback(() => {
    setState((current) => resetSession(current))
  }, [])

  return {
    state,
    currentStage: getCurrentStage(state),
    nextStage,
    previousStage,
    nextSentence,
    previousSentence,
    restartStage,
    restartSession,
  }
}
