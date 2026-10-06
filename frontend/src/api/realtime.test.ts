import { afterEach, describe, expect, it, vi } from 'vitest'

import { createRealtimeClientCredential } from './realtime'

describe('createRealtimeClientCredential', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('requests a client secret only when invoked', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        value: 'ek_test',
        expires_at: 123,
        model: 'gpt-realtime-2.1-mini',
        voice: 'marin',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    expect(fetchMock).not.toHaveBeenCalled()
    await expect(createRealtimeClientCredential()).resolves.toMatchObject({ value: 'ek_test' })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/realtime/client-secret',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('surfaces backend credential errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: vi.fn().mockResolvedValue({ detail: 'OpenAI Realtime is not configured on the server.' }),
    }))

    await expect(createRealtimeClientCredential()).rejects.toThrow('not configured')
  })
})
