import type { ReviewSummary } from './review'
import type { SourceLanguage } from './lesson'
import type { ReviewRating, StudyItem, StudyKind } from './studyBank'

export interface ShadowingSource {
  library_item_id: string
  sentence_id: number
  media_title: string
}

export interface StudyPracticeEvidence {
  study_item_id: string
  kind: StudyKind
  text: string
  source_language: SourceLanguage
  review_status: 'new' | 'due' | 'scheduled'
  due_at: string
  review_count: number
  lapse_count: number
  last_rating: ReviewRating | null
  last_reviewed_at: string | null
  recent_targeted_session_count: number
  recent_used_session_count: number
  recent_use_count: number
  last_used_at: string | null
  speaking_evidence: string | null
  source_sentence_count: number
  attempted_source_sentence_count: number
  stable_source_sentence_count: number
  shadowing_last_practiced_at: string | null
  preferred_shadowing_source: ShadowingSource | null
  guided_practice_count: number
  guided_success_count: number
  guided_failure_count: number
  guided_last_practiced_at: string | null
}

export interface ContinueListeningItem {
  library_item_id: string
  title: string
  source_language: SourceLanguage
  last_position_seconds: number
  current_sentence_id: number | null
  updated_at: string
}

export interface SpeakingSuggestionCandidate {
  speaking_session_id: string
  suggestion_index: number
  source_language: SourceLanguage
  text: string
  meaning: string
  kind: StudyKind
  originating_turn: number
  started_at: string
  target_languages: string[]
}

export interface NextAction {
  type: 'review' | 'speak' | 'shadow' | 'listen' | 'save_speaking_suggestion'
  label: string
  source_language: SourceLanguage | null
  study_item_id: string | null
  library_item_id: string | null
  sentence_id: number | null
  speaking_session_id: string | null
  suggestion_index: number | null
}

export interface IntegrationOverview {
  review: ReviewSummary
  weak_chunks: StudyPracticeEvidence[]
  continue_listening: ContinueListeningItem[]
  speaking_suggestions: SpeakingSuggestionCandidate[]
  next_actions: NextAction[]
  has_study_items: boolean
  has_library_items: boolean
  has_speaking_sessions: boolean
}

export interface SpeakingSuggestionSaveResponse {
  item: StudyItem
  created: boolean
  chunk_memory_synchronized: boolean
}
