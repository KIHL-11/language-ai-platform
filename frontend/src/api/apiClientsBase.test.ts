import { afterEach, describe, expect, it, vi } from 'vitest'

import { createLessonFromUrl } from './lesson'
import { discoverMedia } from './mediaDiscovery'


const okResponse = (body: unknown) => ({
  ok: true,
  status: 200,
  json: vi.fn().mockResolvedValue(body),
})

describe('shared API base URL clients', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses the shared development base for lesson creation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse({ id: 'lesson' }))
    vi.stubGlobal('fetch', fetchMock)

    await createLessonFromUrl({
      url: 'https://www.youtube.com/watch?v=test',
      source_language: 'en',
      target_language: 'zh-CN',
      level: 'B2',
    })

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://127.0.0.1:8000/api/lesson/from-url',
    )
  })

  it('uses the shared development base for media discovery', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await discoverMedia({
      query: 'German travel',
      target_language: 'de',
      training_goal: 'general',
      min_duration_seconds: 60,
      max_duration_seconds: 600,
      limit: 5,
    })

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://127.0.0.1:8000/api/media/discover',
    )
  })
})
