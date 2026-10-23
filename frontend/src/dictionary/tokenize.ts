const LEXICAL_TOKEN = /[\p{L}\p{M}]+(?:['’][\p{L}\p{M}]+)*/gu

export interface TextToken {
  text: string
  lexical: boolean
}

export function tokenizeClickableText(text: string): TextToken[] {
  const tokens: TextToken[] = []
  let cursor = 0
  for (const match of text.matchAll(LEXICAL_TOKEN)) {
    const index = match.index ?? 0
    if (index > cursor) tokens.push({ text: text.slice(cursor, index), lexical: false })
    tokens.push({ text: match[0], lexical: true })
    cursor = index + match[0].length
  }
  if (cursor < text.length) tokens.push({ text: text.slice(cursor), lexical: false })
  return tokens
}
