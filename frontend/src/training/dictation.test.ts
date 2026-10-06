import { describe, expect, it } from 'vitest'

import { compareDictation, normalizeDictationText } from './dictation'

describe('dictation comparison', () => {
  it('ignores capitalization, repeated spaces, and punctuation', () => {
    expect(normalizeDictationText('  Hello,   WORLD!  ')).toBe('hello world')
    expect(compareDictation('Hello, world!', '  hello WORLD  ').accuracy).toBe(100)
  })

  it('reduces accuracy for incorrect and missing words', () => {
    const result = compareDictation(
      'The elephant has a long trunk.',
      'The elephant has long trunk',
    )

    expect(result.accuracy).toBe(83)
    expect(result.missingIncorrectWords).toEqual(['a'])
    expect(result.correctWords).toHaveLength(5)
  })

  it('reports extra words separately', () => {
    const result = compareDictation('That is cool.', 'That is very cool')

    expect(result.accuracy).toBe(75)
    expect(result.extraWords).toEqual(['very'])
  })
})
