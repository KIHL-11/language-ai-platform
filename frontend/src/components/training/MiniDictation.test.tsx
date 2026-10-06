import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { LessonSentence } from '../../types/lesson'
import MiniDictation from './MiniDictation'

const sentence: LessonSentence = {
  id: 1,
  text: 'The elephant has a long trunk.',
  translation: '大象有一条长鼻子。',
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
}

describe('MiniDictation', () => {
  it('hides the target and disables Next before Check', () => {
    render(
      <MiniDictation
        sentence={sentence}
        sentenceIndex={0}
        sentenceCount={2}
        audioUrl={null}
        onNext={vi.fn()}
      />,
    )

    expect(screen.queryByText(sentence.text)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeDisabled()
  })

  it('shows results after Check and Try again resets them', async () => {
    const user = userEvent.setup()
    render(
      <MiniDictation
        sentence={sentence}
        sentenceIndex={0}
        sentenceCount={2}
        audioUrl={null}
        onNext={vi.fn()}
      />,
    )

    const answer = screen.getByLabelText('Your answer')
    await user.type(answer, 'The elephant has trunk')
    await user.click(screen.getByRole('button', { name: 'Check' }))

    expect(screen.getByText(sentence.text)).toBeInTheDocument()
    expect(screen.getByText('67%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(screen.queryByText(sentence.text)).not.toBeInTheDocument()
    expect(answer).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeDisabled()
  })
})
