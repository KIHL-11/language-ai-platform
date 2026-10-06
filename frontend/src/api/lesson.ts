import type { Lesson, LessonLevel, SourceLanguage } from '../types/lesson'

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000'

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
).replace(/\/$/, '')

export interface LessonFromUrlRequest {
  url: string
  source_language: SourceLanguage
  target_language: string
  level: LessonLevel
  start?: number
  end?: number
  vocals?: boolean
}

function getErrorDetail(body: unknown): string | null {
  if (!body || typeof body !== 'object') {
    return null
  }

  const { detail, error } = body as { detail?: unknown; error?: unknown }
  const value = detail ?? error

  if (typeof value === 'string') {
    return value
  }

  if (value !== undefined) {
    return JSON.stringify(value)
  }

  return null
}

async function createHttpError(response: Response): Promise<Error> {
  let detail: string | null = null

  try {
    detail = getErrorDetail(await response.json())
  } catch {
    // The status information below remains useful for non-JSON error responses.
  }

  const reason = detail || response.statusText || 'Unknown error'
  return new Error(`Failed to create lesson (${response.status}): ${reason}`)
}

export async function createLessonFromUrl(
  request: LessonFromUrlRequest,
): Promise<Lesson> {
  const response = await fetch(`${API_BASE_URL}/api/lesson/from-url`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw await createHttpError(response)
  }

  const lesson: Lesson = await response.json()
  return lesson
}
