import type { Lesson, LessonLevel, LessonSource, SourceLanguage, TrainingStage } from './lesson'

export interface LibraryProgress {
  is_training: boolean
  stage_index: number
  sentence_index: number
  completed_stages: TrainingStage[]
  training_order: TrainingStage[]
  lesson_memory_imported: boolean
}

export interface PersistentAudio {
  status: 'available' | 'unavailable'
  filename: string | null
  content_type: string | null
}

export interface LibraryItem {
  id: string
  source_key: string
  created_at: string
  updated_at: string
  lesson: Lesson
  progress: LibraryProgress
  persistent_audio: PersistentAudio
}

export interface LibraryItemSummary {
  id: string
  lesson_id: string
  title: string
  source_url: string
  source_language: SourceLanguage
  target_language: string
  level: LessonLevel
  source: LessonSource
  sentence_count: number
  duration_seconds: number | null
  created_at: string
  updated_at: string
  progress_status: 'not_started' | 'in_progress' | 'complete'
  audio_available: boolean
}
