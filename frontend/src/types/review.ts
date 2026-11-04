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

// Cloud only: the account's stored time zone and server-side daily quota.
export interface ReviewSettings {
  time_zone: string
  configured: boolean
  daily_new_limit: number
  new_cards_today: number
  new_cards_remaining: number
  quota_day: string
  pending_time_zone: string | null
  pending_effective_at: string | null
}

export interface ReviewRatingResult {
  item: StudyItem
  previous_due_at: string
  next_due_at: string
  interval_seconds: number
}

export type { ReviewRating }
