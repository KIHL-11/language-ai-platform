import { describe, expect, it } from 'vitest'

import {
  compareShadowingTiming,
  getMaxRecordingSeconds,
} from './shadowing'

describe('getMaxRecordingSeconds', () => {
  it('uses a minimum limit of 10 seconds', () => {
    expect(getMaxRecordingSeconds(0, 2)).toBe(10)
  })

  it('scales to twice the target duration', () => {
    expect(getMaxRecordingSeconds(4, 10)).toBe(12)
  })

  it('uses an absolute upper limit of 30 seconds', () => {
    expect(getMaxRecordingSeconds(0, 20)).toBe(30)
  })

  it('falls back safely for invalid target durations', () => {
    expect(getMaxRecordingSeconds(5, 2)).toBe(10)
    expect(getMaxRecordingSeconds(Number.NaN, 2)).toBe(10)
  })
})

describe('compareShadowingTiming', () => {
  it('returns 100 for equal durations', () => {
    expect(compareShadowingTiming(1, 3, 2).timingScore).toBeCloseTo(100)
  })

  it('keeps a small difference at a high score', () => {
    expect(compareShadowingTiming(0, 2, 2.1).timingScore).toBeCloseTo(95)
  })

  it('gives a lower score to a large difference', () => {
    expect(compareShadowingTiming(0, 2, 3.5).timingScore).toBeCloseTo(25)
  })

  it('never returns a score below zero', () => {
    expect(compareShadowingTiming(0, 2, 20).timingScore).toBe(0)
  })

  it('never returns a score above 100', () => {
    expect(compareShadowingTiming(0, 2, 0.5).timingScore).toBeLessThanOrEqual(100)
  })

  it('handles zero and invalid target durations safely', () => {
    expect(compareShadowingTiming(2, 2, 1)).toEqual({
      targetDuration: 0,
      recordedDuration: 1,
      durationDifference: 1,
      timingScore: 0,
    })
    expect(compareShadowingTiming(Number.NaN, 2, -1)).toEqual({
      targetDuration: 0,
      recordedDuration: 0,
      durationDifference: 0,
      timingScore: 0,
    })
  })
})
