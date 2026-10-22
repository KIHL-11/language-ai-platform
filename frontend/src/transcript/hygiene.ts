import type { LessonSentence } from '../types/lesson'


const NOISE_LABEL = /\[\s*(?:music|musik|singing|singen|applause|laughter|cheering|inaudible)\s*\]/gi

export function cleanTranscriptText(text: string): string {
  return text.replace(NOISE_LABEL, ' ').replace(/\s+/g, ' ').trim()
}

export function practiceableSentences(sentences: LessonSentence[]): LessonSentence[] {
  return sentences.flatMap((sentence) => {
    const text = cleanTranscriptText(sentence.text)
    return text ? [{ ...sentence, text }] : []
  })
}

export function restoredPracticeIndex(
  original: LessonSentence[],
  practiceable: LessonSentence[],
  requestedId: number | null,
): number {
  if (practiceable.length === 0 || requestedId === null) return 0
  const exact = practiceable.findIndex((sentence) => sentence.id === requestedId)
  if (exact >= 0) return exact
  const originalIndex = original.findIndex((sentence) => sentence.id === requestedId)
  if (originalIndex < 0) return 0
  const originalPositions = new Map(original.map((sentence, index) => [sentence.id, index]))
  const next = practiceable.findIndex(
    (sentence) => (originalPositions.get(sentence.id) ?? -1) > originalIndex,
  )
  return next >= 0 ? next : practiceable.length - 1
}
