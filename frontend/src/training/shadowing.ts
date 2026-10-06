export interface ShadowingTimingResult {
  targetDuration: number
  recordedDuration: number
  durationDifference: number
  timingScore: number
}

function safeDuration(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0
}

export function getMaxRecordingSeconds(
  targetStart: number,
  targetEnd: number,
): number {
  const targetDuration = safeDuration(targetEnd - targetStart)
  return Math.min(30, Math.max(10, targetDuration * 2))
}

export function compareShadowingTiming(
  targetStart: number,
  targetEnd: number,
  recordedDuration: number,
): ShadowingTimingResult {
  const rawTargetDuration = targetEnd - targetStart
  const targetDuration = safeDuration(rawTargetDuration)
  const safeRecordedDuration = safeDuration(recordedDuration)
  const durationDifference = Math.abs(
    safeRecordedDuration - targetDuration,
  )

  if (targetDuration === 0) {
    return {
      targetDuration,
      recordedDuration: safeRecordedDuration,
      durationDifference,
      timingScore: 0,
    }
  }

  const timingScore = Math.min(
    100,
    Math.max(0, 100 - (durationDifference / targetDuration) * 100),
  )

  return {
    targetDuration,
    recordedDuration: safeRecordedDuration,
    durationDifference,
    timingScore,
  }
}
