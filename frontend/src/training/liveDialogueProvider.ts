import {
  LOCAL_DIALOGUE_LEARNER_TURNS,
  type DialogueChunk,
  type DialogueMessage,
  type DialogueScenario,
} from './liveDialogue'
import type { Lesson } from '../types/lesson'

export interface LiveDialogueProviderContext {
  lesson: Lesson
  scenario: DialogueScenario
  candidateChunks: DialogueChunk[]
  messages: DialogueMessage[]
  turnIndex: number
}

export interface ProviderTurn {
  message: DialogueMessage
  completed: boolean
  nextTurnIndex: number
  totalLearnerTurns: number
}

export interface LiveDialogueProvider {
  startSession(context: LiveDialogueProviderContext): Promise<ProviderTurn>
  sendLearnerTurn(
    context: LiveDialogueProviderContext,
    learnerText: string,
  ): Promise<ProviderTurn>
  endSession?(context: LiveDialogueProviderContext): Promise<void>
}

const FOLLOW_UPS = [
  'What detail would you add, and why does it matter to you?',
  'How would you use this idea in your own plans or experience?',
] as const

export class LocalMockDialogueProvider implements LiveDialogueProvider {
  async startSession(context: LiveDialogueProviderContext): Promise<ProviderTurn> {
    return {
      message: {
        id: 'partner-0',
        role: 'partner',
        text: `Imagine we are talking about ${context.lesson.title} in a new situation. What stands out to you?`,
      },
      completed: false,
      nextTurnIndex: 0,
      totalLearnerTurns: LOCAL_DIALOGUE_LEARNER_TURNS,
    }
  }

  async sendLearnerTurn(
    context: LiveDialogueProviderContext,
    _learnerText: string,
  ): Promise<ProviderTurn> {
    const nextTurnIndex = context.turnIndex + 1
    const completed = nextTurnIndex >= LOCAL_DIALOGUE_LEARNER_TURNS

    return {
      message: completed
        ? {
            id: `partner-${nextTurnIndex}`,
            role: 'partner',
            text: 'Thanks for sharing. That gives me a clear picture of your view.',
          }
        : {
            id: `partner-${nextTurnIndex}`,
            role: 'partner',
            text: FOLLOW_UPS[nextTurnIndex - 1],
          },
      completed,
      nextTurnIndex,
      totalLearnerTurns: LOCAL_DIALOGUE_LEARNER_TURNS,
    }
  }
}

export const localMockDialogueProvider = new LocalMockDialogueProvider()
