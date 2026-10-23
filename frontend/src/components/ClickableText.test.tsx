import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { tokenizeClickableText } from '../dictionary/tokenize'
import ClickableText from './ClickableText'


describe('ClickableText', () => {
  it('preserves punctuation and tokenizes English contractions and German Unicode', () => {
    const text = "Häuser, größer – don't stop!"
    expect(tokenizeClickableText(text).map((value) => value.text).join('')).toBe(text)
    expect(tokenizeClickableText(text).filter((value) => value.lexical).map((value) => value.text)).toEqual([
      'Häuser', 'größer', "don't", 'stop',
    ])
  })

  it('is keyboard accessible and returns bounded word context', async () => {
    const user = userEvent.setup()
    const onLookup = vi.fn()
    render(<ClickableText text="Wir gehen." sentence="Wir gehen." language="de" onLookup={onLookup} />)
    await user.tab()
    const word = screen.getByRole('button', { name: 'Look up Wir' })
    expect(word).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onLookup).toHaveBeenCalledWith(
      { word: 'Wir', sentence: 'Wir gehen.', language: 'de' },
      word,
    )
  })
})
