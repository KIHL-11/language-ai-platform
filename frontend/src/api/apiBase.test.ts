import { describe, expect, it } from 'vitest'

import { resolveApiBaseUrl } from './apiBase'

describe('resolveApiBaseUrl', () => {
  it('keeps the local FastAPI fallback during Vite development', () => {
    expect(resolveApiBaseUrl(undefined, true)).toBe('http://127.0.0.1:8000')
  })

  it('uses same-origin requests in a production build', () => {
    expect(resolveApiBaseUrl(undefined, false)).toBe('')
  })

  it('honors and normalizes an explicit VITE_API_BASE_URL override', () => {
    expect(resolveApiBaseUrl('https://language.example.test/', false)).toBe(
      'https://language.example.test',
    )
  })
})
