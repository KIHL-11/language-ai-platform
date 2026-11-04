import { apiFetch } from '../auth/client'
import { API_BASE_URL } from './apiBase'
import { isStudyItem } from './studyBank'
import type { ReviewFilters, ReviewRating, ReviewRatingResult, ReviewSettings, ReviewSummary } from '../types/review'
import type { StudyItem } from '../types/studyBank'


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export class ReviewApiError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(message: string, status: number, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
  }
}

// The structured `detail.code` of an error response, when there is one.
async function errorCode(response: Response): Promise<string | null> {
  try {
    const value: unknown = await response.json()
    const detail = isRecord(value) ? value.detail : null
    return isRecord(detail) && typeof detail.code === 'string' ? detail.code : null
  } catch {
    return null
  }
}

async function responseJson(response: Response, action: string): Promise<unknown> {
  if (!response.ok) throw new ReviewApiError(`${action} (${response.status}).`, response.status, await errorCode(response))
  try {
    return await response.json()
  } catch {
    throw new ReviewApiError(`${action} returned an unexpected response.`, response.status)
  }
}

function params(filters: ReviewFilters): URLSearchParams {
  const result = new URLSearchParams({ user_id: filters.userId })
  if (filters.language) result.set('language', filters.language)
  if (filters.kind) result.set('kind', filters.kind)
  return result
}

export async function getReviewSummary(filters: Omit<ReviewFilters, 'kind'>): Promise<ReviewSummary> {
  const value = await responseJson(
    await apiFetch(`${API_BASE_URL}/api/review/summary?${params(filters)}`),
    'Loading Daily Review summary failed',
  )
  if (!isRecord(value) || !['due_words', 'due_chunks', 'new_words', 'new_chunks', 'active_words', 'active_chunks']
    .every((key) => Number.isInteger(value[key]) && Number(value[key]) >= 0)) {
    throw new Error('Daily Review summary returned an unexpected response.')
  }
  return value as unknown as ReviewSummary
}

export async function getReviewQueue(filters: ReviewFilters): Promise<StudyItem[]> {
  const value = await responseJson(
    await apiFetch(`${API_BASE_URL}/api/review/queue?${params(filters)}`),
    'Loading Daily Review queue failed',
  )
  if (!isRecord(value) || !Array.isArray(value.items) || !value.items.every(isStudyItem)) {
    throw new Error('Daily Review queue returned an unexpected response.')
  }
  return value.items
}

function isSettings(value: unknown): value is ReviewSettings {
  return isRecord(value) && typeof value.time_zone === 'string' && typeof value.configured === 'boolean' &&
    Number.isInteger(value.daily_new_limit) && Number.isInteger(value.new_cards_today) &&
    Number.isInteger(value.new_cards_remaining) && typeof value.quota_day === 'string' &&
    (value.pending_time_zone === null || typeof value.pending_time_zone === 'string') &&
    (value.pending_effective_at === null || typeof value.pending_effective_at === 'string')
}

export async function getReviewSettings(userId: string): Promise<ReviewSettings> {
  const value = await responseJson(
    await apiFetch(`${API_BASE_URL}/api/review/settings?${new URLSearchParams({ user_id: userId })}`),
    'Loading Daily Review settings failed',
  )
  if (!isSettings(value)) throw new Error('Daily Review settings returned an unexpected response.')
  return value
}

// Sets the account's IANA time zone. The server applies it at once only if no
// new card was started today, otherwise at the next local midnight.
export async function updateReviewTimeZone(userId: string, timeZone: string): Promise<ReviewSettings> {
  const value = await responseJson(await apiFetch(`${API_BASE_URL}/api/review/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, time_zone: timeZone }),
  }), 'Saving the Daily Review time zone failed')
  if (!isSettings(value)) throw new Error('Daily Review settings returned an unexpected response.')
  return value
}

// The server's stable code when a first rating would exceed today's quota.
export function isDailyQuotaExhausted(error: unknown): boolean {
  return error instanceof ReviewApiError && error.status === 429 && error.code === 'daily_new_card_quota_exhausted'
}

export async function rateReviewItem(
  userId: string,
  itemId: string,
  rating: ReviewRating,
  expectedReviewCount: number,
): Promise<ReviewRatingResult> {
  const value = await responseJson(await apiFetch(
    `${API_BASE_URL}/api/review/items/${encodeURIComponent(itemId)}/rate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        rating,
        expected_review_count: expectedReviewCount,
      }),
    },
  ), 'Rating Daily Review item failed')
  if (!isRecord(value) || !isStudyItem(value.item) ||
    typeof value.previous_due_at !== 'string' || typeof value.next_due_at !== 'string' ||
    !Number.isInteger(value.interval_seconds) || Number(value.interval_seconds) < 0) {
    throw new Error('Daily Review rating returned an unexpected response.')
  }
  return value as unknown as ReviewRatingResult
}
