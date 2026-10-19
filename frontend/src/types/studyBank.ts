import type { SourceLanguage } from './lesson'

export type StudyKind = 'word' | 'chunk'
export type StudyQuality = 'recommended' | 'review' | 'rejected'
export type ReviewRating = 'again' | 'hard' | 'good' | 'easy'

export interface ReviewState {
  due_at: string
  interval_seconds: number
  ease_factor: number
  repetition_count: number
  lapse_count: number
  review_count: number
  again_count: number
  hard_count: number
  good_count: number
  easy_count: number
  last_reviewed_at: string | null
  last_rating: ReviewRating | null
}

export interface StudyProvenance {
  library_item_id: string
  lesson_id: string
  source_url: string
  media_title: string
  sentence_id: number
  sentence_text: string
  sentence_translation: string
  start: number
  end: number
}

export interface StudyItemInput {
  user_id: string
  kind: StudyKind
  source_language: SourceLanguage
  target_language: string
  text: string
  meaning: string
  lemma: string | null
  part_of_speech: string | null
  example: string | null
  usage: string | null
  note: string | null
  provenance: StudyProvenance[]
}

export interface StudyItem extends StudyItemInput {
  id: string
  normalized_text: string
  active: boolean
  created_at: string
  updated_at: string
  review: ReviewState
}

export interface StudyCandidate {
  candidate_id: string
  kind: StudyKind
  source_language: SourceLanguage
  target_language: string
  text: string
  normalized_text: string
  meaning: string
  lemma: string | null
  part_of_speech: string | null
  example: string | null
  usage: string | null
  quality: StudyQuality
  quality_reasons: string[]
  provenance: StudyProvenance
  already_saved: boolean
  study_item_id: string | null
}
