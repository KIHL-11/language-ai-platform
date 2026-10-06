export const RETELL_TARGET_MIN_SECONDS = 30
export const RETELL_TARGET_MAX_SECONDS = 45

export type RetellTimingStatus = 'too_short' | 'target_range' | 'long'

export function evaluateRetellDuration(
  durationSeconds: number,
): RetellTimingStatus {
  const duration =
    Number.isFinite(durationSeconds) && durationSeconds > 0
      ? durationSeconds
      : 0

  if (duration < RETELL_TARGET_MIN_SECONDS) {
    return 'too_short'
  }
  if (duration <= RETELL_TARGET_MAX_SECONDS) {
    return 'target_range'
  }
  return 'long'
}
