import { describe, expect, it } from 'vitest'

import type { LessonSentence } from '../types/lesson'
import { generateComprehensionQuestion } from './comprehension'

const sentence: LessonSentence = {
  id: 4,
  text: 'The weather is getting colder.',
  translation: '天气正在变冷。',
  start: 0,
  end: 2,
  keywords: [{
    word: 'colder',
    meaning: '更冷',
    lemma: 'cold',
    pos: 'adjective',
    example: 'The weather is getting colder.',
  }],
  chunks: [{
    text: 'getting colder',
    meaning: '正在变冷',
    usage: 'describing gradual change',
  }],
  grammar: '',
  training: {
    blind_listening: true,
    dictation: true,
    shadowing: true,
    retell: true,
  },
  ai_status: 'ok',
}

describe('comprehension question generation', () => {
  it('creates a deterministic question with one correct and two wrong options', () => {
    const first = generateComprehensionQuestion(sentence)
    const second = generateComprehensionQuestion(sentence)

    expect(first).toEqual(second)
    expect(first.question).toContain('getting colder')
    expect(first.options).toHaveLength(3)
    expect(first.options[first.answerIndex]).toBe('正在变冷')
    expect(new Set(first.options).size).toBe(3)
  })
})
