import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  LEARNER_ID_STORAGE_KEY,
  getOrCreateLearnerId,
} from './learnerSession'


const FIRST_ID = '123e4567-e89b-42d3-a456-426614174000'
const SECOND_ID = '987e6543-e21b-43d3-b654-426614174111'

describe('local learner session', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('creates and persists an opaque UUID when storage is empty', () => {
    const randomUUID = vi.fn(() => FIRST_ID)

    const learnerId = getOrCreateLearnerId({
      storage: localStorage,
      crypto: { randomUUID },
    })

    expect(learnerId).toBe(FIRST_ID)
    expect(localStorage.getItem(LEARNER_ID_STORAGE_KEY)).toBe(FIRST_ID)
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })

  it('reuses a valid learner ID across sessions', () => {
    localStorage.setItem(LEARNER_ID_STORAGE_KEY, FIRST_ID)
    const randomUUID = vi.fn(() => SECOND_ID)

    expect(
      getOrCreateLearnerId({
        storage: localStorage,
        crypto: { randomUUID },
      }),
    ).toBe(FIRST_ID)
    expect(randomUUID).not.toHaveBeenCalled()
  })

  it.each(['', 'learner@example.com', 'not-a-uuid', '123e4567-e89b-12d3-a456-426614174000'])(
    'replaces malformed stored value %j safely',
    (stored) => {
      localStorage.setItem(LEARNER_ID_STORAGE_KEY, stored)

      const learnerId = getOrCreateLearnerId({
        storage: localStorage,
        crypto: { randomUUID: () => SECOND_ID },
      })

      expect(learnerId).toBe(SECOND_ID)
      expect(localStorage.getItem(LEARNER_ID_STORAGE_KEY)).toBe(SECOND_ID)
    },
  )

  it('uses random bytes for a standards-shaped UUID fallback', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index))
      return bytes
    })

    const learnerId = getOrCreateLearnerId({
      storage: localStorage,
      crypto: { getRandomValues },
    })

    expect(learnerId).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f')
    expect(getRandomValues).toHaveBeenCalledTimes(1)
  })

  it('does not make a network request while creating an identity', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    getOrCreateLearnerId({
      storage: localStorage,
      crypto: { randomUUID: () => FIRST_ID },
    })

    expect(fetchMock).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
