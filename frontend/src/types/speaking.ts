import type { LessonLevel, SourceLanguage } from './lesson'

export type SpeakingMode = 'review_weak' | 'media' | 'custom'
export type SpeakingProviderId = 'gemini-live' | 'openai-realtime'
export type SpeakingEndReason = 'user' | 'time_limit' | 'provider' | 'error' | 'page_exit'
export type SpeakingStatus = 'active' | 'completed' | 'interrupted' | 'failed'

export interface ProviderCapabilities { voice: boolean; text_input: boolean; barge_in: boolean; input_transcript: boolean; output_transcript: boolean; tools: boolean }
export interface SpeakingProviderInfo { id: SpeakingProviderId; configured: boolean; model: string; capabilities: ProviderCapabilities }
export interface SpeakingTarget { study_item_id: string; text: string; meaning: string }
export interface TargetUsage extends SpeakingTarget { use_count: number; first_used_turn: number | null; last_used_turn: number | null; evidence: string[] }
export interface SpeakingWord extends SpeakingTarget {}
export interface SpeakingTurn { sequence: number; role: 'learner' | 'agent'; text: string; created_at: string; provider_turn_id: string | null }
export interface SpeakingCorrection { learner_text: string; corrected_text: string; short_explanation: string; category: 'grammar' | 'word_choice' | 'target_chunk' | 'clarity' | 'other' }
export interface SpeakingSuggestion { text: string; meaning: string; kind: 'word' | 'chunk'; short_usage_note: string; originating_turn: number }
export interface SpeakingContext {
  language: SourceLanguage; level: LessonLevel; mode: SpeakingMode; session_goal: string
  target_chunks: SpeakingTarget[]; support_words: SpeakingWord[]; library_item_id: string | null
  media_title: string | null; media_sentences: Array<{ sentence_id: number; text: string; translation: string; start: number; end: number }>
  system_prompt: string
}
export interface SpeakingSession {
  id: string; user_id: string; language: SourceLanguage; level: LessonLevel; mode: SpeakingMode
  provider: SpeakingProviderId | 'topic-mock'; model: string; requested_duration_seconds: 180 | 300 | 600
  library_item_id: string | null; media_title_snapshot: string | null; goal: string | null
  started_at: string; ended_at: string | null; status: SpeakingStatus; event_count: number
  target_chunks: SpeakingTarget[]; support_words: SpeakingWord[]; learner_turn_count: number; agent_turn_count: number
  turns: SpeakingTurn[]; corrections: SpeakingCorrection[]; suggestions: SpeakingSuggestion[]
  target_usage: TargetUsage[]; end_reason: SpeakingEndReason | null
}
export interface SpeakingSetup {
  user_id: string; language: SourceLanguage; level: LessonLevel; mode: SpeakingMode
  provider: SpeakingProviderId; duration_seconds: 180 | 300 | 600; library_item_id?: string | null
  goal?: string | null; target_limit?: number; target_ids?: string[] | null
}
