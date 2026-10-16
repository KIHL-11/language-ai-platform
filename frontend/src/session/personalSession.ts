import { createTrainingState, TRAINING_STAGE_LABELS } from '../training/trainingMachine'
import type { TrainingSessionState } from '../training/trainingMachine'
import type {
  Chunk,
  Keyword,
  Lesson,
  LessonSentence,
  TrainingStage,
} from '../types/lesson'

export const PERSONAL_SESSION_STORAGE_KEY = 'language_ai_personal_session_v1'
const PERSONAL_SESSION_VERSION = 1 as const
const MAX_TEXT_LENGTH = 20_000
const MAX_SENTENCES = 2_000

export interface PersonalSession {
  version: typeof PERSONAL_SESSION_VERSION
  lesson: Lesson
  isTraining: boolean
  lessonMemoryImported: boolean
  trainingState: TrainingSessionState
}

interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown, allowEmpty = false): string | null {
  if (typeof value !== 'string' || value.length > MAX_TEXT_LENGTH) return null
  return allowEmpty || value.trim().length > 0 ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function parseKeyword(value: unknown): Keyword | null {
  if (!isRecord(value)) return null
  const word = stringValue(value.word)
  const meaning = stringValue(value.meaning, true)
  const lemma = stringValue(value.lemma, true)
  const pos = stringValue(value.pos, true)
  const example = stringValue(value.example, true)
  if (!word || meaning === null || lemma === null || pos === null || example === null) return null
  return { word, meaning, lemma, pos, example }
}

function parseChunk(value: unknown): Chunk | null {
  if (!isRecord(value)) return null
  const text = stringValue(value.text)
  const meaning = stringValue(value.meaning, true)
  const usage = stringValue(value.usage, true)
  if (!text || meaning === null || usage === null) return null
  return { text, meaning, usage }
}

function parseObjectArray<T>(
  value: unknown,
  parser: (item: unknown) => T | null,
): T[] | null {
  if (!Array.isArray(value) || value.length > 2_000) return null
  const result: T[] = []
  for (const item of value) {
    const parsed = parser(item)
    if (parsed === null) return null
    result.push(parsed)
  }
  return result
}

function parseSentence(value: unknown): LessonSentence | null {
  if (!isRecord(value) || !isRecord(value.training)) return null
  const id = numberValue(value.id)
  const text = stringValue(value.text)
  const translation = stringValue(value.translation, true)
  const start = numberValue(value.start)
  const end = numberValue(value.end)
  const grammar = stringValue(value.grammar, true)
  const keywords = parseObjectArray(value.keywords, parseKeyword)
  const chunks = parseObjectArray(value.chunks, parseChunk)
  const blindListening = booleanValue(value.training.blind_listening)
  const dictation = booleanValue(value.training.dictation)
  const shadowing = booleanValue(value.training.shadowing)
  const retell = booleanValue(value.training.retell)
  if (
    id === null || !Number.isInteger(id) || !text || translation === null ||
    start === null || end === null || start < 0 || end < start || grammar === null ||
    keywords === null || chunks === null ||
    blindListening === null || dictation === null || shadowing === null || retell === null ||
    (value.ai_status !== 'ok' && value.ai_status !== 'failed')
  ) return null
  return {
    id,
    text,
    translation,
    start,
    end,
    keywords,
    chunks,
    grammar,
    training: {
      blind_listening: blindListening,
      dictation,
      shadowing,
      retell,
    },
    ai_status: value.ai_status,
  }
}

function isTrainingStage(value: unknown): value is TrainingStage {
  return typeof value === 'string' && value in TRAINING_STAGE_LABELS
}

function parseTrainingOrder(value: unknown): TrainingStage[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > 7) return null
  if (!value.every(isTrainingStage) || new Set(value).size !== value.length) return null
  return [...value]
}

function parseEnabled(value: unknown): { enabled: boolean } | null {
  if (!isRecord(value) || typeof value.enabled !== 'boolean') return null
  return { enabled: value.enabled }
}

function parseLesson(value: unknown): Lesson | null {
  if (!isRecord(value) || !isRecord(value.training_plan) || !isRecord(value.ai)) return null
  const id = stringValue(value.id)
  const title = stringValue(value.title)
  const sourceUrl = stringValue(value.source_url)
  const targetLanguage = stringValue(value.target_language)
  const provider = stringValue(value.ai.provider)
  const sentences = parseObjectArray(value.sentences, parseSentence)
  const trainingOrder = parseTrainingOrder(value.training_order)
  const activeRecall = parseEnabled(value.training_plan.active_recall)
  const activeRecallRecord = isRecord(value.training_plan.active_recall)
    ? value.training_plan.active_recall
    : null
  const activeRecallCount = activeRecallRecord
    ? numberValue(activeRecallRecord.count)
    : null
  const blindListening = parseEnabled(value.training_plan.blind_listening)
  const comprehension = parseEnabled(value.training_plan.comprehension)
  const miniDictation = parseEnabled(value.training_plan.mini_dictation)
  const shadowing = parseEnabled(value.training_plan.shadowing)
  const retell = parseEnabled(value.training_plan.retell)
  const liveDialogue = parseEnabled(value.training_plan.live_dialogue)
  const audioUrl = value.audio_url === null ? null : stringValue(value.audio_url)
  const safeAudioUrl = value.audio_url === null || (audioUrl !== null &&
    audioUrl.startsWith('/') && !audioUrl.startsWith('//') &&
    !audioUrl.toLowerCase().startsWith('/data:') &&
    !audioUrl.toLowerCase().startsWith('/blob:') && audioUrl.length <= 2_048
  )
  if (
    !id || !title || !sourceUrl || !targetLanguage || !provider ||
    !sentences || sentences.length > MAX_SENTENCES || !trainingOrder ||
    !activeRecall || activeRecallCount === null || !Number.isInteger(activeRecallCount) || activeRecallCount < 0 ||
    !blindListening || !comprehension || !miniDictation || !shadowing || !retell || !liveDialogue ||
    !safeAudioUrl ||
    (value.source_language !== 'en' && value.source_language !== 'de') ||
    (value.level !== 'B1' && value.level !== 'B2' && value.level !== 'C1') ||
    (value.source !== 'subtitles' && value.source !== 'whisper') ||
    (value.ai.status !== 'complete' && value.ai.status !== 'partial' && value.ai.status !== 'failed')
  ) return null
  return {
    id,
    title,
    source_url: sourceUrl,
    source_language: value.source_language,
    target_language: targetLanguage,
    level: value.level,
    audio_url: audioUrl,
    source: value.source,
    sentences,
    training_plan: {
      active_recall: { ...activeRecall, count: activeRecallCount },
      blind_listening: blindListening,
      comprehension,
      mini_dictation: miniDictation,
      shadowing,
      retell,
      live_dialogue: liveDialogue,
    },
    training_order: trainingOrder,
    ai: { provider, status: value.ai.status },
  }
}

function arraysEqual<T>(left: T[], right: T[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

function parseTrainingState(
  value: unknown,
  lesson: Lesson,
): TrainingSessionState | null {
  if (!isRecord(value)) return null
  const order = parseTrainingOrder(value.order)
  const stageIndex = numberValue(value.stageIndex)
  const sentenceIndex = numberValue(value.sentenceIndex)
  if (
    !order || !arraysEqual(order, lesson.training_order) ||
    stageIndex === null || !Number.isInteger(stageIndex) || stageIndex < 0 || stageIndex >= order.length ||
    sentenceIndex === null || !Number.isInteger(sentenceIndex) || sentenceIndex < 0 ||
    (lesson.sentences.length === 0 ? sentenceIndex !== 0 : sentenceIndex >= lesson.sentences.length) ||
    !Array.isArray(value.completedStages) || !isRecord(value.completedSentences)
  ) return null

  const completedStages = value.completedStages
  if (
    !completedStages.every(isTrainingStage) ||
    new Set(completedStages).size !== completedStages.length ||
    !completedStages.every((stage, index) => order[index] === stage && index <= stageIndex)
  ) return null

  const completedSentences: Partial<Record<TrainingStage, number[]>> = {}
  for (const [stage, indexes] of Object.entries(value.completedSentences)) {
    if (!isTrainingStage(stage) || !order.includes(stage) || !Array.isArray(indexes)) return null
    if (
      !indexes.every((index) => Number.isInteger(index) && index >= 0 && index < lesson.sentences.length) ||
      new Set(indexes).size !== indexes.length
    ) return null
    completedSentences[stage] = [...indexes]
  }
  return { order, stageIndex, sentenceIndex, completedStages: [...completedStages], completedSentences }
}

function parseSession(value: unknown): PersonalSession | null {
  if (
    !isRecord(value) || value.version !== PERSONAL_SESSION_VERSION ||
    typeof value.isTraining !== 'boolean' || typeof value.lessonMemoryImported !== 'boolean'
  ) return null
  const lesson = parseLesson(value.lesson)
  if (!lesson) return null
  const trainingState = parseTrainingState(value.trainingState, lesson)
  if (!trainingState) return null
  return {
    version: PERSONAL_SESSION_VERSION,
    lesson,
    isTraining: value.isTraining,
    lessonMemoryImported: value.lessonMemoryImported,
    trainingState,
  }
}

function browserStorage(): StorageLike | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export function createPersonalSession(lessonValue: Lesson): PersonalSession {
  const lesson = parseLesson(lessonValue)
  if (!lesson) throw new Error('Invalid lesson for personal session persistence.')
  return {
    version: PERSONAL_SESSION_VERSION,
    lesson,
    isTraining: false,
    lessonMemoryImported: false,
    trainingState: createTrainingState(lesson.training_order),
  }
}

export function loadPersonalSession(storage = browserStorage()): PersonalSession | null {
  if (!storage) return null
  try {
    const stored = storage.getItem(PERSONAL_SESSION_STORAGE_KEY)
    if (stored === null) return null
    const session = parseSession(JSON.parse(stored))
    if (!session) storage.removeItem(PERSONAL_SESSION_STORAGE_KEY)
    return session
  } catch {
    try {
      storage.removeItem(PERSONAL_SESSION_STORAGE_KEY)
    } catch {
      // Storage can be unavailable or read-only; persistence stays optional.
    }
    return null
  }
}

export function savePersonalSession(
  value: PersonalSession,
  storage = browserStorage(),
): boolean {
  if (!storage) return false
  const session = parseSession(value)
  if (!session) return false
  try {
    storage.setItem(PERSONAL_SESSION_STORAGE_KEY, JSON.stringify(session))
    return true
  } catch {
    return false
  }
}
