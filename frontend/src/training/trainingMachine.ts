import type { TrainingStage } from '../types/lesson'

export const TRAINING_STAGE_LABELS: Record<TrainingStage, string> = {
  active_recall: 'Active Recall',
  blind_listening: 'Blind Listening',
  comprehension: 'Comprehension',
  mini_dictation: 'Mini Dictation',
  shadowing: 'Shadowing',
  retell: 'Retell',
  live_dialogue: 'Live Dialogue',
}

export interface TrainingSessionState {
  order: TrainingStage[]
  stageIndex: number
  sentenceIndex: number
  completedStages: TrainingStage[]
  completedSentences: Partial<Record<TrainingStage, number[]>>
}

function assertValidOrder(order: TrainingStage[]): void {
  if (order.length === 0) {
    throw new Error('A training session requires at least one stage.')
  }

  const seen = new Set<TrainingStage>()
  for (const stage of order) {
    if (!(stage in TRAINING_STAGE_LABELS)) {
      throw new Error(`Invalid training stage: ${String(stage)}`)
    }
    if (seen.has(stage)) {
      throw new Error(`Duplicate training stage: ${stage}`)
    }
    seen.add(stage)
  }
}

function appendUnique<T>(items: T[], item: T): T[] {
  return items.includes(item) ? items : [...items, item]
}

export function createTrainingState(
  order: TrainingStage[],
): TrainingSessionState {
  assertValidOrder(order)
  return {
    order: [...order],
    stageIndex: 0,
    sentenceIndex: 0,
    completedStages: [],
    completedSentences: {},
  }
}

export function getCurrentStage(
  state: TrainingSessionState,
): TrainingStage {
  return state.order[state.stageIndex]
}

export function nextStage(
  state: TrainingSessionState,
): TrainingSessionState {
  const currentStage = getCurrentStage(state)
  const completedStages = appendUnique(state.completedStages, currentStage)

  if (state.stageIndex === state.order.length - 1) {
    return { ...state, completedStages }
  }

  return {
    ...state,
    stageIndex: state.stageIndex + 1,
    sentenceIndex: 0,
    completedStages,
  }
}

export function previousStage(
  state: TrainingSessionState,
): TrainingSessionState {
  if (state.stageIndex === 0) {
    return state
  }
  return {
    ...state,
    stageIndex: state.stageIndex - 1,
    sentenceIndex: 0,
  }
}

export function nextSentence(
  state: TrainingSessionState,
  sentenceCount: number,
): TrainingSessionState {
  if (sentenceCount <= 0) {
    return state
  }

  const currentStage = getCurrentStage(state)
  const stageSentences = state.completedSentences[currentStage] ?? []
  const completedSentences = {
    ...state.completedSentences,
    [currentStage]: appendUnique(stageSentences, state.sentenceIndex),
  }

  if (state.sentenceIndex >= sentenceCount - 1) {
    return {
      ...state,
      completedSentences,
      completedStages: appendUnique(state.completedStages, currentStage),
    }
  }

  return {
    ...state,
    sentenceIndex: state.sentenceIndex + 1,
    completedSentences,
  }
}

export function previousSentence(
  state: TrainingSessionState,
): TrainingSessionState {
  if (state.sentenceIndex === 0) {
    return state
  }
  return { ...state, sentenceIndex: state.sentenceIndex - 1 }
}

export function restartStage(
  state: TrainingSessionState,
): TrainingSessionState {
  const currentStage = getCurrentStage(state)
  const completedSentences = { ...state.completedSentences }
  delete completedSentences[currentStage]

  return {
    ...state,
    sentenceIndex: 0,
    completedStages: state.completedStages.filter(
      (stage) => stage !== currentStage,
    ),
    completedSentences,
  }
}

export function restartSession(
  state: TrainingSessionState,
): TrainingSessionState {
  return createTrainingState(state.order)
}
