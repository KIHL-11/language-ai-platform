import { normalizeDictationText } from './dictation'
import type { Lesson } from '../types/lesson'

export type DialogueRole = 'partner' | 'learner' | 'system'

export interface DialogueMessage {
  id: string
  role: DialogueRole
  text: string
}

export interface DialogueScenario {
  id: string
  title: string
  description: string
  learnerGoal: string
}

export interface DialogueChunk {
  id: string
  text: string
  meaning: string
}

export type DialogueSessionStatus =
  | 'intro'
  | 'starting'
  | 'conversation'
  | 'review'
  | 'complete'
  | 'error'

export interface DialogueSessionState {
  status: DialogueSessionStatus
  turnIndex: number
  messages: DialogueMessage[]
  usedChunkIds: string[]
  completed: boolean
}

export const LOCAL_DIALOGUE_LEARNER_TURNS = 3

export function generateDialogueScenario(lesson: Lesson): DialogueScenario {
  const title = lesson.title.trim() || 'this lesson'
  const firstKeyword = lesson.sentences
    .flatMap((sentence) => sentence.keywords)
    .find((keyword) => keyword.word.trim())
  const firstChunk = selectDialogueChunks(lesson)[0]
  const focus = firstKeyword?.meaning.trim() || firstKeyword?.word.trim()

  return {
    id: `lesson-${lesson.id}-transfer`,
    title: `A new conversation about ${title}`,
    description: `You meet a conversation partner after studying “${title}”. Discuss the topic from your own perspective in a new situation.`,
    learnerGoal: firstChunk
      ? `Share an opinion and one personal detail. Try to reuse “${firstChunk.text}”.`
      : focus
        ? `Share an opinion and one personal detail connected to ${focus}.`
        : 'Share an opinion, add one important detail, and respond to follow-up questions.',
  }
}

export function selectDialogueChunks(lesson: Lesson): DialogueChunk[] {
  const seen = new Set<string>()
  const selected: DialogueChunk[] = []

  for (const sentence of lesson.sentences) {
    for (let index = 0; index < sentence.chunks.length; index += 1) {
      const chunk = sentence.chunks[index]
      const normalized = normalizeDictationText(chunk.text)
      if (!normalized || seen.has(normalized)) {
        continue
      }

      seen.add(normalized)
      selected.push({
        id: `${sentence.id}-${index}`,
        text: chunk.text,
        meaning: chunk.meaning,
      })

      if (selected.length === 5) {
        return selected
      }
    }
  }

  return selected
}

export function detectReusedChunks(
  learnerText: string,
  candidateChunks: DialogueChunk[],
): string[] {
  const normalizedLearner = normalizeDictationText(learnerText)
  if (!normalizedLearner) {
    return []
  }

  const paddedLearner = ` ${normalizedLearner} `
  return candidateChunks
    .filter((chunk) => {
      const phrase = normalizeDictationText(chunk.text)
      return phrase.length > 0 && paddedLearner.includes(` ${phrase} `)
    })
    .map((chunk) => chunk.id)
}

export function collectReusedChunkIds(
  messages: DialogueMessage[],
  candidateChunks: DialogueChunk[],
): string[] {
  const used = new Set<string>()
  for (const message of messages) {
    if (message.role !== 'learner') {
      continue
    }
    for (const id of detectReusedChunks(message.text, candidateChunks)) {
      used.add(id)
    }
  }
  return [...used]
}

export function upsertDialogueMessage(
  messages: DialogueMessage[],
  incoming: DialogueMessage,
): DialogueMessage[] {
  const index = messages.findIndex((message) => message.id === incoming.id)
  if (index === -1) {
    return [...messages, incoming]
  }
  if (
    messages[index].role === incoming.role &&
    messages[index].text === incoming.text
  ) {
    return messages
  }
  const next = [...messages]
  next[index] = incoming
  return next
}

export function isDialogueComplete(
  learnerTurnCount: number,
  requiredLearnerTurns = LOCAL_DIALOGUE_LEARNER_TURNS,
): boolean {
  if (!Number.isFinite(learnerTurnCount) || !Number.isFinite(requiredLearnerTurns)) {
    return false
  }
  return requiredLearnerTurns > 0 && learnerTurnCount >= requiredLearnerTurns
}
