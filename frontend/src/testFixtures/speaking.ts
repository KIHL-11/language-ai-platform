import type { SpeakingContext, SpeakingProviderInfo, SpeakingSession } from '../types/speaking'

export const speakingContext: SpeakingContext = {
  language: 'en', level: 'B2', mode: 'review_weak', session_goal: 'Practice naturally.',
  target_chunks: [{ study_item_id: 'target-1', text: 'as far as I know', meaning: '据我所知' }],
  support_words: [], library_item_id: null, media_title: null, media_sentences: [], system_prompt: 'SYSTEM INSTRUCTIONS\nPractice naturally.',
}

export const speakingSession: SpeakingSession = {
  id: 'session-1', user_id: '00000000-0000-4000-8000-000000000001', language: 'en', level: 'B2', mode: 'review_weak',
  provider: 'gemini-live', model: 'gemini-3.8-live', requested_duration_seconds: 300,
  library_item_id: null, media_title_snapshot: null, goal: null,
  started_at: '2026-10-20T10:00:00Z', ended_at: null, status: 'active', event_count: 0,
  target_chunks: speakingContext.target_chunks, support_words: [], learner_turn_count: 0, agent_turn_count: 0,
  turns: [], corrections: [], suggestions: [],
  target_usage: [{ ...speakingContext.target_chunks[0], use_count: 0, first_used_turn: null, last_used_turn: null, evidence: [] }],
  end_reason: null,
}

export const speakingProviders: SpeakingProviderInfo[] = [
  { id: 'gemini-live', configured: true, model: 'gemini-3.8-live', capabilities: { voice: true, text_input: true, barge_in: true, input_transcript: true, output_transcript: true, tools: true } },
  { id: 'openai-realtime', configured: true, model: 'gpt-realtime-2.1-mini', capabilities: { voice: true, text_input: false, barge_in: true, input_transcript: true, output_transcript: true, tools: false } },
]
