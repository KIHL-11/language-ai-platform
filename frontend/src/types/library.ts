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

export type ListeningMode = 'listen' | 'blind' | 'dictation'
export type ListeningPlaybackRate = 0.5 | 0.75 | 1 | 1.25

export interface ListeningProgress {
  last_position_seconds: number
  current_sentence_id: number | null
  playback_rate: ListeningPlaybackRate
  source_visible: boolean
  translation_visible: boolean
  mode: ListeningMode
}

export type ShadowingWordStatus = 'matched' | 'missed' | 'extra' | 'substituted'

export interface ShadowingMetrics {
  recognized_text: string
  target_word_count: number
  recognized_word_count: number
  matched_count: number
  missed_count: number
  extra_count: number
  substituted_count: number
  transcript_match_percent: number
  reference_speech_duration_ms: number | null
  attempt_speech_duration_ms: number | null
  duration_delta_ms: number | null
  duration_delta_ratio: number | null
  start_delay_ms: number | null
  word_timing_mae_ms: number | null
  pause_mae_ms: number | null
  aligned_timed_word_count: number
  timing_reference_available: boolean
  timing_unavailable_reason: string | null
  sync_target_met: boolean
  aligned_words: Array<{ status: ShadowingWordStatus; target: string | null; recognized: string | null }>
}

export interface SentenceShadowingProgress {
  attempt_count: number
  target_hit_count: number
  consecutive_target_hits: number
  last_attempt_at: string | null
  last_metrics: ShadowingMetrics | null
  best_transcript_match_percent: number | null
  best_duration_delta_ratio: number | null
  best_abs_start_delay_ms: number | null
  best_word_timing_mae_ms: number | null
  best_pause_mae_ms: number | null
}

export interface ShadowingProgress {
  last_sentence_id: number | null
  last_practiced_at: string | null
  sentences: Record<string, SentenceShadowingProgress>
}

export interface LibraryItem {
  id: string
  source_key: string
  created_at: string
  updated_at: string
  lesson: Lesson
  progress: LibraryProgress
  persistent_audio: PersistentAudio
  listening_progress: ListeningProgress
  shadowing_progress: ShadowingProgress
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
  last_position_seconds: number
  has_listening_progress: boolean
  shadowing_attempt_count: number
  shadowing_target_sentence_count: number
  shadowing_stable_sentence_count: number
  shadowing_last_practiced_at: string | null
}

export interface ShadowingAttemptResponse {
  sentence_id: number
  progress: SentenceShadowingProgress
  metrics: ShadowingMetrics
  sync_target_met: boolean
  stable_sync_achieved: boolean
}
