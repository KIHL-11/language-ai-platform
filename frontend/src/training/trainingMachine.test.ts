import { describe, expect, it } from 'vitest'

import type { TrainingStage } from '../types/lesson'
import {
  createTrainingState,
  getCurrentStage,
  nextSentence,
  nextStage,
  restartSession,
  restartStage,
} from './trainingMachine'

const backendOrder: TrainingStage[] = [
  'active_recall',
  'blind_listening',
  'comprehension',
  'mini_dictation',
  'shadowing',
  'retell',
  'live_dialogue',
]

describe('training state machine', () => {
  it('starts at the first backend-provided stage', () => {
    const state = createTrainingState(backendOrder)

    expect(getCurrentStage(state)).toBe('active_recall')
    expect(state.sentenceIndex).toBe(0)
    expect(state.completedStages).toEqual([])
  })

  it('progresses one step at a time in the provided order', () => {
    let state = createTrainingState([
      'blind_listening',
      'active_recall',
      'mini_dictation',
    ])

    state = nextStage(state)
    expect(getCurrentStage(state)).toBe('active_recall')
    expect(state.completedStages).toEqual(['blind_listening'])

    state = nextStage(state)
    expect(getCurrentStage(state)).toBe('mini_dictation')
    expect(state.stageIndex).toBe(2)
  })

  it('tracks sentences and restarts the current stage predictably', () => {
    let state = createTrainingState(backendOrder)
    state = nextStage(state)
    state = nextSentence(state, 3)

    expect(state.sentenceIndex).toBe(1)
    expect(state.completedSentences.blind_listening).toEqual([0])

    state = restartStage(state)
    expect(state.sentenceIndex).toBe(0)
    expect(state.completedSentences.blind_listening).toBeUndefined()
  })

  it('restart session returns to the first stage', () => {
    let state = createTrainingState(backendOrder)
    state = nextStage(nextStage(state))
    state = restartSession(state)

    expect(getCurrentStage(state)).toBe('active_recall')
    expect(state.stageIndex).toBe(0)
    expect(state.completedStages).toEqual([])
  })

  it('rejects invalid runtime stage names', () => {
    expect(() =>
      createTrainingState(['not_a_stage'] as unknown as TrainingStage[]),
    ).toThrow('Invalid training stage')
  })
})
