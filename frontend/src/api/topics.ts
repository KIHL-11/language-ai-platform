import { API_BASE_URL } from './apiBase'
import type { LessonLevel, SourceLanguage } from '../types/lesson'
import type { SpeakingTopic, TopicAttemptState, TopicFeedback, TopicPreview } from '../types/topics'

async function json<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw Object.assign(new Error(`${action} (${response.status})`), { status: response.status })
  return response.json() as Promise<T>
}

export async function listTopics(language: SourceLanguage, level: LessonLevel): Promise<SpeakingTopic[]> {
  const result = await json<{ topics: SpeakingTopic[] }>(await fetch(`${API_BASE_URL}/api/topics?language=${language}&level=${level}`), 'Loading topic catalog failed')
  return result.topics
}

export async function previewTopic(topicId: string, userId: string): Promise<TopicPreview> {
  return json(await fetch(`${API_BASE_URL}/api/topics/${encodeURIComponent(topicId)}/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, target_limit: 5 }),
  }), 'Loading topic preview failed')
}

export async function startTopicAttempt(topicId: string, userId: string, targetIds: string[], requestKey = crypto.randomUUID(), version = 1): Promise<TopicAttemptState> {
  return json(await fetch(`${API_BASE_URL}/api/topics/${encodeURIComponent(topicId)}/attempts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, target_ids: targetIds, topic_version: version, idempotency_key: requestKey }),
  }), 'Starting topic practice failed')
}

export async function sendTopicTurn(topicId: string, state: TopicAttemptState, userId: string, text: string): Promise<{ state: TopicAttemptState; agent_reply: string }> {
  return json(await fetch(`${API_BASE_URL}/api/topics/${encodeURIComponent(topicId)}/attempts/${encodeURIComponent(state.session_id)}/turns`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, expected_event_count: state.event_count, text, topic_version: state.topic_version, idempotency_key: `${state.session_id}:turn:${state.event_count}` }),
  }), 'Sending topic turn failed')
}

export async function finishTopicAttempt(topicId: string, state: TopicAttemptState, userId: string, interrupted: boolean): Promise<{ state: TopicAttemptState; feedback: TopicFeedback }> {
  return json(await fetch(`${API_BASE_URL}/api/topics/${encodeURIComponent(topicId)}/attempts/${encodeURIComponent(state.session_id)}/finish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, expected_event_count: state.event_count, interrupted, topic_version: state.topic_version, idempotency_key: `${state.session_id}:finish:${state.event_count}` }),
  }), 'Finishing topic practice failed')
}

export interface SavedTopicAttempt {
  state: TopicAttemptState
  turns: Array<{ role: 'learner' | 'agent'; text: string }>
  feedback: TopicFeedback | null
  status: 'active' | 'completed' | 'interrupted' | 'failed'
}

export async function listTopicAttempts(topicId: string, userId: string): Promise<string[]> {
  const result = await json<{ session_ids: string[] }>(await fetch(`${API_BASE_URL}/api/topics/${encodeURIComponent(topicId)}/attempts?user_id=${encodeURIComponent(userId)}`), 'Loading attempts failed')
  if (!Array.isArray(result.session_ids)) throw new Error('Invalid saved attempt list')
  return result.session_ids
}

export async function getTopicAttempt(topicId: string, sessionId: string, userId: string): Promise<SavedTopicAttempt> {
  return json(await fetch(`${API_BASE_URL}/api/topics/${encodeURIComponent(topicId)}/attempts/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`), 'Loading attempt failed')
}
