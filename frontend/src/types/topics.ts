import type { LessonLevel, SourceLanguage } from './lesson'
import type { SpeakingCorrection, SpeakingTarget, TargetUsage } from './speaking'

export interface TopicCheckpoint { id: string; label: string; evidence_terms: string[] }
export interface SpeakingTopic {
  topic_id: string; version: number; language: SourceLanguage; level: LessonLevel
  label: string; scenario: string; goal: string; learner_role: string; partner_role: string
  success_criteria: TopicCheckpoint[]; guided_prompts: string[]; support_expressions: string[]
  state: 'published'
}
export interface TopicPreview {
  topic: SpeakingTopic; target_chunks: SpeakingTarget[]; lifecycle: string[]; evidence_notice: string
}
export interface TopicAttemptState {
  session_id: string; topic_id: string; topic_version: number
  phase: 'live' | 'ended' | 'feedback' | 'saved'; event_count: number
  learner_turn_count: number; target_usage: TargetUsage[]; prompt: string | null
}
export interface TopicCheckpointResult { checkpoint_id: string; label: string; observed: boolean; evidence: string | null }
export interface TopicFeedback {
  topic_id: string; topic_version: number; completion: 'completed' | 'interrupted'
  learner_turn_count: number; checkpoints: TopicCheckpointResult[]
  target_usage: TargetUsage[]; corrections: SpeakingCorrection[]; limitations: string[]
}
