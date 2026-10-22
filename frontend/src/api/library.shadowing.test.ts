import { afterEach, describe, expect, it, vi } from 'vitest'

import { ShadowingApiError, submitShadowingAttempt } from './library'


describe('shadowing attempt upload', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('uses a filename matching the browser recording container', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => ({ detail: 'test stop' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(submitShadowingAttempt('item', 1, 0, new Blob(['audio'], { type: 'audio/mp4' }), 2))
      .rejects.toMatchObject({ status: 500 })
    const form = fetchMock.mock.calls[0][1].body as FormData
    const audio = form.get('audio') as File
    expect(audio.name).toBe('shadowing-attempt.m4a')
    expect(audio.type).toBe('audio/mp4')
  })

  it('distinguishes a transport upload failure from backend analysis errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(submitShadowingAttempt('item', 1, 0, new Blob(['audio'], { type: 'audio/webm' }), 2))
      .rejects.toEqual(expect.objectContaining<Partial<ShadowingApiError>>({
        status: 0,
        message: 'Recording upload failed.',
      }))
  })
})
