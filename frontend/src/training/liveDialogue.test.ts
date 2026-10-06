import { describe, expect, it } from 'vitest'

import type { Lesson } from '../types/lesson'
import {
  collectReusedChunkIds,
  detectReusedChunks,
  generateDialogueScenario,
  isDialogueComplete,
  selectDialogueChunks,
  upsertDialogueMessage,
  type DialogueChunk,
} from './liveDialogue'

const lesson: Lesson = {
  id: 'dialogue-logic',
  title: 'Making weekend plans',
  source_url: 'https://example.com/lesson',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [
    {
      id: 1,
      text: 'Would you like to get coffee this weekend?',
      translation: '这个周末你想喝咖啡吗？',
      start: 0,
      end: 3,
      keywords: [{ word: 'weekend', meaning: '周末', lemma: 'weekend', pos: 'noun', example: '' }],
      chunks: [
        { text: 'would you like to', meaning: '你想不想', usage: '' },
        { text: 'this weekend', meaning: '这个周末', usage: '' },
        { text: 'after work', meaning: '下班后', usage: '' },
      ],
      grammar: '',
      training: { blind_listening: true, dictation: true, shadowing: true, retell: true },
      ai_status: 'ok',
    },
    {
      id: 2,
      text: 'That sounds good.',
      translation: '听起来不错。',
      start: 3,
      end: 5,
      keywords: [],
      chunks: [
        { text: 'that sounds good', meaning: '听起来不错', usage: '' },
        { text: 'let me know', meaning: '告诉我', usage: '' },
        { text: 'see you soon', meaning: '回头见', usage: '' },
      ],
      grammar: '',
      training: { blind_listening: true, dictation: true, shadowing: true, retell: true },
      ai_status: 'ok',
    },
  ],
  training_plan: {
    active_recall: { enabled: false, count: 0 },
    blind_listening: { enabled: false },
    comprehension: { enabled: false },
    mini_dictation: { enabled: false },
    shadowing: { enabled: false },
    retell: { enabled: false },
    live_dialogue: { enabled: true },
  },
  training_order: ['live_dialogue'],
  ai: { provider: 'mock', status: 'complete' },
}

describe('live dialogue logic', () => {
  it('generates the same lesson-related scenario every time', () => {
    const first = generateDialogueScenario(lesson)
    expect(generateDialogueScenario(lesson)).toEqual(first)
    expect(first.title).toContain(lesson.title)
    expect(first.description).not.toContain(lesson.sentences[0].text)
  })

  it('selects at most five unique lesson chunks', () => {
    const chunks = selectDialogueChunks(lesson)
    expect(chunks).toHaveLength(5)
    expect(new Set(chunks.map((chunk) => chunk.text)).size).toBe(5)
  })

  it('detects normalized exact phrase reuse', () => {
    const chunks: DialogueChunk[] = [
      { id: 'one', text: 'Would you like to', meaning: '' },
      { id: 'two', text: 'after work', meaning: '' },
    ]
    expect(detectReusedChunks('WOULD YOU LIKE TO meet after work?', chunks)).toEqual(['one', 'two'])
    expect(detectReusedChunks('The aftermath was unexpected.', chunks)).toEqual([])
  })

  it('does not count expressions that only appear in partner messages', () => {
    const chunks: DialogueChunk[] = [{ id: 'one', text: 'after work', meaning: '' }]
    expect(collectReusedChunkIds([
      { id: 'p1', role: 'partner', text: 'Could we meet after work?' },
      { id: 'l1', role: 'learner', text: 'Tomorrow is fine.' },
    ], chunks)).toEqual([])
  })

  it('completes only after the required learner turns', () => {
    expect(isDialogueComplete(2, 3)).toBe(false)
    expect(isDialogueComplete(3, 3)).toBe(true)
    expect(isDialogueComplete(4, 3)).toBe(true)
    expect(isDialogueComplete(Number.NaN, 3)).toBe(false)
  })

  it('upserts transcript messages by stable provider id', () => {
    const first = upsertDialogueMessage([], { id: 'turn-1', role: 'learner', text: 'Draft' })
    const duplicate = upsertDialogueMessage(first, { id: 'turn-1', role: 'learner', text: 'Draft' })
    const completed = upsertDialogueMessage(duplicate, { id: 'turn-1', role: 'learner', text: 'Completed text' })

    expect(duplicate).toBe(first)
    expect(completed).toEqual([{ id: 'turn-1', role: 'learner', text: 'Completed text' }])
  })
})
