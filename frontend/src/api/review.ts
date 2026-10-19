import { API_BASE_URL } from './apiBase'
import { isStudyItem } from './studyBank'
import type { ReviewFilters, ReviewRating, ReviewRatingResult, ReviewSummary } from '../types/review'
import type { StudyItem } from '../types/studyBank'


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export class ReviewApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function responseJson(response: Response, action: string): Promise<unknown> {
  if (!response.ok) throw new ReviewApiError(`${action} (${response.status}).`, response.status)
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
    await fetch(`${API_BASE_URL}/api/review/summary?${params(filters)}`),
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
    await fetch(`${API_BASE_URL}/api/review/queue?${params(filters)}`),
    'Loading Daily Review queue failed',
  )
  if (!isRecord(value) || !Array.isArray(value.items) || !value.items.every(isStudyItem)) {
    throw new Error('Daily Review queue returned an unexpected response.')
  }
  return value.items
}

export async function rateReviewItem(
  userId: string,
  itemId: string,
  rating: ReviewRating,
  expectedReviewCount: number,
): Promise<ReviewRatingResult> {
  const value = await responseJson(await fetch(
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
