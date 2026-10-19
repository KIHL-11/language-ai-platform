import type { SourceLanguage } from './lesson'
import type { ReviewRating, StudyItem, StudyKind } from './studyBank'

export interface ReviewSummary {
  due_words: number
  due_chunks: number
  new_words: number
  new_chunks: number
  active_words: number
  active_chunks: number
}

export interface ReviewFilters {
  userId: string
  language?: SourceLanguage
  kind?: StudyKind
}

export interface ReviewRatingResult {
  item: StudyItem
  previous_due_at: string
  next_due_at: string
  interval_seconds: number
}

export type { ReviewRating }
