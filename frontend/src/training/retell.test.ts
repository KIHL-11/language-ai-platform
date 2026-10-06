import { describe, expect, it } from 'vitest'

import {
  evaluateRetellDuration,
  RETELL_TARGET_MAX_SECONDS,
  RETELL_TARGET_MIN_SECONDS,
} from './retell'

describe('evaluateRetellDuration', () => {
  it.each([0, 20, 29.9])('classifies %s seconds as too short', (duration) => {
    expect(evaluateRetellDuration(duration)).toBe('too_short')
  })

  it.each([30, 40, 45])('classifies %s seconds in the target range', (duration) => {
    expect(evaluateRetellDuration(duration)).toBe('target_range')
  })

  it('classifies durations over 45 seconds as long', () => {
    expect(evaluateRetellDuration(45.1)).toBe('long')
  })

  it('handles invalid values safely', () => {
    expect(evaluateRetellDuration(Number.NaN)).toBe('too_short')
    expect(evaluateRetellDuration(Number.POSITIVE_INFINITY)).toBe('too_short')
    expect(evaluateRetellDuration(-1)).toBe('too_short')
  })

  it('exports the target window', () => {
    expect(RETELL_TARGET_MIN_SECONDS).toBe(30)
    expect(RETELL_TARGET_MAX_SECONDS).toBe(45)
  })
})
