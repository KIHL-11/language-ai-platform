export type SourceLanguage = 'en' | 'de'

export type LessonLevel = 'B1' | 'B2' | 'C1'

export type LessonSource = 'subtitles' | 'whisper'

export type AnalysisStatus = 'ok' | 'failed'

export type LessonAIStatus = 'complete' | 'partial' | 'failed'

export type TrainingStage =
  | 'active_recall'
  | 'blind_listening'
  | 'comprehension'
  | 'mini_dictation'
  | 'shadowing'
  | 'retell'
  | 'live_dialogue'

export interface Keyword {
  word: string
  meaning: string
  lemma: string
  pos: string
  example: string
}

export interface Chunk {
  text: string
  meaning: string
  usage: string
}

export interface SentenceTraining {
  blind_listening: boolean
  dictation: boolean
  shadowing: boolean
  retell: boolean
}

export interface LessonSentence {
  id: number
  text: string
  translation: string
  start: number
  end: number
  keywords: Keyword[]
  chunks: Chunk[]
  grammar: string
  training: SentenceTraining
  ai_status: AnalysisStatus
}

export interface EnabledTraining {
  enabled: boolean
}

export interface ActiveRecallTraining extends EnabledTraining {
  count: number
}

export interface TrainingPlan {
  active_recall: ActiveRecallTraining
  blind_listening: EnabledTraining
  comprehension: EnabledTraining
  mini_dictation: EnabledTraining
  shadowing: EnabledTraining
  retell: EnabledTraining
  live_dialogue: EnabledTraining
}

export interface LessonAI {
  provider: string
  status: LessonAIStatus
}

export interface Lesson {
  id: string
  title: string
  source_url: string
  source_language: SourceLanguage
  target_language: string
  level: LessonLevel
  audio_url: string | null
  source: LessonSource
  sentences: LessonSentence[]
  training_plan: TrainingPlan
  training_order: TrainingStage[]
  ai: LessonAI
}
