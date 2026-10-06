import { describe, expect, it, vi } from 'vitest'

import type { Lesson } from '../types/lesson'
import { generateDialogueScenario } from './liveDialogue'
import {
  LocalMockDialogueProvider,
  type LiveDialogueProviderContext,
} from './liveDialogueProvider'

const lesson = {
  id: 'provider-test',
  title: 'Me at the zoo',
  sentences: [],
} as unknown as Lesson

function context(turnIndex = 0): LiveDialogueProviderContext {
  return {
    lesson,
    scenario: generateDialogueScenario(lesson),
    candidateChunks: [],
    messages: [],
    turnIndex,
  }
}

describe('LocalMockDialogueProvider', () => {
  it('returns a deterministic first partner turn', async () => {
    const provider = new LocalMockDialogueProvider()
    expect(await provider.startSession(context())).toEqual(await provider.startSession(context()))
    expect((await provider.startSession(context())).message.role).toBe('partner')
  })

  it('progresses deterministically and completes after three learner turns', async () => {
    const provider = new LocalMockDialogueProvider()
    const firstFollowUp = await provider.sendLearnerTurn(context(0), 'First answer')
    const secondFollowUp = await provider.sendLearnerTurn(context(1), 'Second answer')
    const closingTurn = await provider.sendLearnerTurn(context(2), 'Third answer')

    expect(firstFollowUp.completed).toBe(false)
    expect(firstFollowUp.nextTurnIndex).toBe(1)
    expect(secondFollowUp.completed).toBe(false)
    expect(closingTurn.completed).toBe(true)
    expect(closingTurn.nextTurnIndex).toBe(3)
  })

  it('does not make external requests', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const provider = new LocalMockDialogueProvider()
    await provider.startSession(context())
    await provider.sendLearnerTurn(context(0), 'Local only')
    expect(fetchSpy).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })
})
