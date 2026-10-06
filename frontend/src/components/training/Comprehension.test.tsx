import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { generateComprehensionQuestion } from '../../training/comprehension'
import type { LessonSentence } from '../../types/lesson'
import Comprehension from './Comprehension'

const sentence: LessonSentence = {
  id: 2,
  text: 'The weather is getting colder.',
  translation: '天气正在变冷。',
  start: 0,
  end: 2,
  keywords: [],
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

describe('Comprehension', () => {
  it('submits a generated answer and explains it with the original sentence', async () => {
    const user = userEvent.setup()
    const question = generateComprehensionQuestion(sentence, [sentence])
    const correctAnswer = question.options[question.answerIndex]

    render(
      <Comprehension
        sentence={sentence}
        lessonSentences={[sentence]}
        sentenceIndex={0}
        sentenceCount={2}
        audioUrl={null}
        onNext={vi.fn()}
      />,
    )

    expect(screen.getByText(question.question)).toBeInTheDocument()
    expect(screen.queryByText(sentence.text)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeDisabled()

    await user.click(screen.getByLabelText(correctAnswer))
    await user.click(screen.getByRole('button', { name: 'Submit answer' }))

    expect(screen.getByText('Correct')).toBeInTheDocument()
    expect(screen.getByText(sentence.text)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeEnabled()
  })
})
