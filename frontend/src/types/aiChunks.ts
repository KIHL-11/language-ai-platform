import type { SourceLanguage } from './lesson'
import type { StudyItem } from './studyBank'

export type CandidateState = 'proposed' | 'accepted' | 'edited_and_accepted' | 'rejected' | 'expired'

export interface AiChunkEvidence {
  source_type: 'manual_text' | 'library_sentence'
  quote: string
  source_label: string
  evidence_start: number
  evidence_end: number
}

export interface AiChunkCandidate {
  candidate_id: string
  state: CandidateState
  version: number
  source_language: SourceLanguage
  text: string
  normalized_identity: string
  suggested_meaning_zh: string
  usage_note: string
  example: string
  explanation_of_value: string
  quality_flags: string[]
  evidence: AiChunkEvidence
  proposal_model: string
  proposal_version: string
  existing_study_item_id: string | null
  existing_study_item_active: boolean | null
  accepted_study_item_id: string | null
}

export interface AiChunkBatch {
  batch_id: string
  user_id: string
  source_language: SourceLanguage
  created_at: string
  candidates: AiChunkCandidate[]
}

export interface CandidateEdit {
  text: string
  suggested_meaning_zh: string
  usage_note: string
  example: string
}

export interface CandidateDecisionResult {
  candidate: AiChunkCandidate
  study_item: StudyItem | null
  created: boolean
  warnings: string[]
}
