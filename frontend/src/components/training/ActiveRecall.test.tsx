import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { LessonSentence } from '../../types/lesson'
import ActiveRecall from './ActiveRecall'

const sentence: LessonSentence = {
  id: 1,
  text: 'I have worked here for two years.',
  translation: '我已经在这里工作两年了。',
  start: 0,
  end: 2,
  keywords: [],
  chunks: [
    {
      text: 'for two years',
      meaning: '持续两年',
      usage: 'duration',
    },
  ],
  grammar: '',
  training: {
    blind_listening: true,
    dictation: true,
    shadowing: true,
    retell: true,
  },
  ai_status: 'ok',
}

describe('ActiveRecall', () => {
  it('hides the target and locks Next until Check', async () => {
    const user = userEvent.setup()
    render(
      <ActiveRecall
        sentence={sentence}
        sentenceIndex={0}
        sentenceCount={2}
        onNext={vi.fn()}
      />,
    )

    expect(screen.queryByText(sentence.text)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeDisabled()

    await user.type(
      screen.getByLabelText('Rebuild the original sentence'),
      sentence.text,
    )
    await user.click(screen.getByRole('button', { name: 'Check' }))

    expect(screen.getAllByText(sentence.text)).toHaveLength(3)
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeEnabled()
    expect(screen.getByText('100%')).toBeInTheDocument()
  })
})
