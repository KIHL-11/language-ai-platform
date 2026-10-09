import type { SourceLanguage } from './lesson'

export type TrainingGoal =
  | 'general'
  | 'blind_listening'
  | 'comprehension'
  | 'mini_dictation'
  | 'shadowing'
  | 'retell'

export interface MediaDiscoveryRequest {
  query: string
  target_language: SourceLanguage
  training_goal: TrainingGoal
  min_duration_seconds: number
  max_duration_seconds: number
  limit: number
}

export interface MediaCandidate {
  provider: 'youtube'
  provider_id: string
  url: string
  title: string
  channel: string | null
  duration_seconds: number
  thumbnail: string | null
  is_live: boolean
  availability: 'public' | 'private' | 'unavailable' | 'unknown'
  subtitle_tracks: Array<{ language: string; has_human: boolean; has_automatic: boolean }>
  suitability: { total: number; reasons: string[] }
}
