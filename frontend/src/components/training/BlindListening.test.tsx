import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { LessonSentence } from '../../types/lesson'
import BlindListening from './BlindListening'

const sentences: LessonSentence[] = [
  {
    id: 1,
    text: 'First hidden transcript.',
    translation: '第一句。',
    start: 0,
    end: 1,
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
  {
    id: 2,
    text: 'Second hidden transcript.',
    translation: '第二句。',
    start: 1,
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
]

describe('BlindListening', () => {
  it('keeps the transcript hidden until reveal', async () => {
    const user = userEvent.setup()
    render(
      <BlindListening
        sentence={sentences[0]}
        sentenceIndex={0}
        sentenceCount={2}
        audioUrl={null}
        onNext={vi.fn()}
      />,
    )

    expect(screen.queryByText(sentences[0].text)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Reveal transcript' }))

    expect(screen.getByText(sentences[0].text)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next sentence' })).toBeEnabled()
  })

  it('hides the transcript again on the next sentence', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [index, setIndex] = useState(0)
      return (
        <BlindListening
          sentence={sentences[index]}
          sentenceIndex={index}
          sentenceCount={2}
          audioUrl={null}
          onNext={() => setIndex(1)}
        />
      )
    }

    render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Reveal transcript' }))
    await user.click(screen.getByRole('button', { name: 'Next sentence' }))

    expect(screen.queryByText(sentences[0].text)).not.toBeInTheDocument()
    expect(screen.queryByText(sentences[1].text)).not.toBeInTheDocument()
    expect(screen.getByText('Sentence 2 of 2')).toBeInTheDocument()
  })
})
