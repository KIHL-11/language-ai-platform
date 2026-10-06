import { describe, expect, it } from 'vitest'

import type { Lesson } from '../types/lesson'
import { buildLiveDialoguePrompt } from './liveDialoguePrompt'

const lesson = {
  id: 'prompt',
  title: 'Wochenendpläne',
  source_language: 'de',
  level: 'B1',
} as Lesson

describe('buildLiveDialoguePrompt', () => {
  it('builds a language-learning prompt from lesson context', () => {
    const prompt = buildLiveDialoguePrompt(
      lesson,
      {
        id: 'scenario',
        title: 'Ein neues Treffen',
        description: 'Arrange a meeting after work.',
        learnerGoal: 'Agree on a time.',
      },
      [{ id: '1', text: 'Hast du Lust', meaning: 'would you like' }],
    )

    expect(prompt).toContain('Speak in German.')
    expect(prompt).toContain('CEFR B1')
    expect(prompt).toContain('Hast du Lust')
    expect(prompt).toContain('normally ask one question at a time')
    expect(prompt).toContain('Do not grade them')
    expect(prompt).not.toContain('pronunciation score:')
  })
})
