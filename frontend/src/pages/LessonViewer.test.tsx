import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { Lesson } from '../types/lesson'
import LessonViewer from './LessonViewer'

const lesson: Lesson = {
  id: 'direct-dev-training',
  title: 'Direct development training',
  source_url: 'https://example.com/video',
  source_language: 'en',
  target_language: 'zh-CN',
  level: 'B2',
  audio_url: null,
  source: 'subtitles',
  sentences: [
    {
      id: 1,
      text: 'A sentence for development testing.',
      translation: '用于开发测试的句子。',
      start: 0,
      end: 2,
      keywords: [],
      chunks: [],
      grammar: '',
      training: {
        blind_listening: true,
        dictation: true,
        shadowing: true,
        retell: true,
      },
      ai_status: 'ok',
    },
  ],
  training_plan: {
    active_recall: { enabled: true, count: 1 },
    blind_listening: { enabled: true },
    comprehension: { enabled: true },
    mini_dictation: { enabled: true },
    shadowing: { enabled: true },
    retell: { enabled: true },
    live_dialogue: { enabled: true },
  },
  training_order: ['active_recall'],
  ai: { provider: 'mock', status: 'complete' },
}

afterEach(() => {
  vi.restoreAllMocks()
  window.history.replaceState({}, '', '/')
})

it('opens TrainingSession directly when a valid development stage is requested', () => {
  window.history.replaceState({}, '', '/?stage=shadowing')

  render(<LessonViewer lesson={lesson} />)

  expect(screen.getByRole('heading', { name: 'Shadowing', level: 1 })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Back to lesson' })).toBeInTheDocument()
})
