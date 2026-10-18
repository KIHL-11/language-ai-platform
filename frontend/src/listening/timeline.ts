import type { LessonSentence } from '../types/lesson'

export type ListeningLoop =
  | { type: 'none' }
  | { type: 'sentence' }
  | { type: 'custom_ab'; start: number; end: number }

export const MINIMUM_LOOP_SECONDS = 0.5

export function activeSentenceIndex(
  sentences: LessonSentence[],
  currentTime: number,
  previousIndex: number,
): number {
  const exact = sentences.findIndex(
    (sentence) => sentence.start <= currentTime && currentTime < sentence.end,
  )
  if (exact >= 0) return exact
  if (previousIndex >= 0 && previousIndex < sentences.length) return previousIndex
  return sentences.findIndex((sentence) => sentence.start > currentTime) === 0 ? 0 : -1
}

export function validCustomLoop(
  start: number | null,
  end: number,
  duration: number | null,
): { start: number; end: number } | null {
  if (start === null || !Number.isFinite(start) || !Number.isFinite(end)) return null
  const safeStart = Math.max(0, duration === null ? start : Math.min(start, duration))
  const safeEnd = Math.max(0, duration === null ? end : Math.min(end, duration))
  return safeEnd - safeStart >= MINIMUM_LOOP_SECONDS
    ? { start: safeStart, end: safeEnd }
    : null
}
