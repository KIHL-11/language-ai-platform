import { describe, expect, it } from 'vitest'

import type { LessonSentence } from '../types/lesson'
import { cleanTranscriptText, practiceableSentences, restoredPracticeIndex } from './hygiene'

const sentence = (id: number, text: string): LessonSentence => ({
  id, text, translation: '', start: id, end: id + 1, keywords: [], chunks: [], grammar: '',
  training: { blind_listening: true, dictation: true, shadowing: true, retell: true },
  ai_status: 'ok',
})

describe('transcript hygiene', () => {
  it('removes supported case-insensitive adjacent labels while preserving speech', () => {
    expect(cleanTranscriptText('  Hallo [MUSIK] [singing] Welt [ApPlAuSe]  ')).toBe('Hallo Welt')
    expect(cleanTranscriptText("Don't [music] stop.")).toBe("Don't stop.")
  })

  it('filters only empty cleaned sentences and preserves ids and timing', () => {
    const original = [sentence(1, '[music] [APPLAUSE]'), sentence(2, 'Hi'), sentence(3, '[inaudible] there')]
    const visible = practiceableSentences(original)
    expect(visible.map(({ id, text, start }) => ({ id, text, start }))).toEqual([
      { id: 2, text: 'Hi', start: 2 },
      { id: 3, text: 'there', start: 3 },
    ])
    expect(original[0].text).toBe('[music] [APPLAUSE]')
  })

  it('resolves a hidden deep link to the next practiceable sentence, then the previous one', () => {
    const original = [sentence(1, 'before'), sentence(2, '[music]'), sentence(3, 'after'), sentence(4, '[laughter]')]
    const visible = practiceableSentences(original)
    expect(restoredPracticeIndex(original, visible, 2)).toBe(1)
    expect(restoredPracticeIndex(original, visible, 4)).toBe(1)
  })
})
