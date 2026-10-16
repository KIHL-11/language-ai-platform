import { beforeEach, describe, expect, it } from 'vitest'

import { createTrainingState, nextStage } from '../training/trainingMachine'
import { personalLesson } from '../testFixtures/personalLesson'
import type { TrainingStage } from '../types/lesson'
import {
  PERSONAL_SESSION_STORAGE_KEY,
  createPersonalSession,
  loadPersonalSession,
  savePersonalSession,
} from './personalSession'

describe('personal session persistence', () => {
  beforeEach(() => localStorage.clear())

  it('round-trips a valid lesson and canonical training progression', () => {
    let trainingState = createTrainingState(personalLesson.training_order)
    trainingState = nextStage(nextStage(trainingState))
    const session = {
      ...createPersonalSession(personalLesson),
      isTraining: true,
      lessonMemoryImported: true,
      trainingState,
    }

    expect(savePersonalSession(session)).toBe(true)
    expect(loadPersonalSession()).toEqual(session)
  })

  it.each([
    ['malformed JSON', '{broken'],
    ['unsupported version', JSON.stringify({ version: 2 })],
    [
      'invalid lesson',
      JSON.stringify({
        ...createPersonalSession(personalLesson),
        lesson: { ...personalLesson, title: null },
      }),
    ],
    [
      'invalid stage',
      JSON.stringify({
        ...createPersonalSession(personalLesson),
        trainingState: {
          ...createTrainingState(personalLesson.training_order),
          order: ['invalid_stage'],
        },
      }),
    ],
    [
      'out-of-range sentence',
      JSON.stringify({
        ...createPersonalSession(personalLesson),
        trainingState: {
          ...createTrainingState(personalLesson.training_order),
          stageIndex: 1,
          sentenceIndex: 99,
        },
      }),
    ],
  ])('discards %s safely', (_label, stored) => {
    localStorage.setItem(PERSONAL_SESSION_STORAGE_KEY, stored)

    expect(loadPersonalSession()).toBeNull()
    expect(localStorage.getItem(PERSONAL_SESSION_STORAGE_KEY)).toBeNull()
  })

  it('retains completed history only when it is coherent with the current stage', () => {
    const state = {
      ...createTrainingState(personalLesson.training_order),
      stageIndex: 3,
      sentenceIndex: 2,
      completedStages: [
        'active_recall',
        'blind_listening',
        'comprehension',
      ] as TrainingStage[],
      completedSentences: {
        blind_listening: [0, 1, 2],
        comprehension: [0, 1, 2],
        mini_dictation: [0, 1],
      },
    }
    const session = {
      ...createPersonalSession(personalLesson),
      isTraining: true,
      trainingState: state,
    }

    savePersonalSession(session)

    expect(loadPersonalSession()?.trainingState).toEqual(state)
  })

  it('serializes an allowlisted lesson shape without secrets or binary media', () => {
    const unsafeLesson = {
      ...personalLesson,
      api_key: 'secret-value',
      realtime_client_secret: 'ephemeral-secret',
      microphone_recording: new Blob(['audio']),
    }

    savePersonalSession(createPersonalSession(unsafeLesson))

    const stored = localStorage.getItem(PERSONAL_SESSION_STORAGE_KEY) ?? ''
    expect(stored).not.toContain('secret-value')
    expect(stored).not.toContain('ephemeral-secret')
    expect(stored).not.toContain('microphone_recording')
    expect(stored).not.toContain('Blob')
  })

  it.each(['data:audio/wav;base64,AAAA', 'blob:http://localhost/audio'])(
    'rejects non-reconstructable audio URL %s',
    (audioUrl) => {
      expect(() =>
        createPersonalSession({ ...personalLesson, audio_url: audioUrl }),
      ).toThrow('Invalid lesson')
    },
  )
})
