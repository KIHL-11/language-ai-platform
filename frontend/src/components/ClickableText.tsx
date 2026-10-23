import type { SourceLanguage } from '../types/lesson'
import { tokenizeClickableText } from '../dictionary/tokenize'

interface ClickableTextProps {
  text: string
  sentence: string
  language: SourceLanguage
  onLookup: (selection: { word: string; sentence: string; language: SourceLanguage }) => void
  className?: string
}

export default function ClickableText({ text, sentence, language, onLookup, className }: ClickableTextProps) {
  return <span className={className}>{tokenizeClickableText(text).map((token, index) => token.lexical ? (
    <button
      key={`${index}-${token.text}`}
      type="button"
      aria-label={`Look up ${token.text}`}
      onClick={(event) => {
        event.stopPropagation()
        onLookup({ word: token.text, sentence, language })
      }}
      className="rounded-sm underline decoration-[#91aaa1] decoration-dotted underline-offset-4 hover:bg-[#e2eee9] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#176b5b]"
    >{token.text}</button>
  ) : <span key={`${index}-${token.text}`}>{token.text}</span>)}</span>
}
