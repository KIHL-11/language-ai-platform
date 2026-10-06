import type { Keyword } from '../types/lesson'

interface KeywordListProps {
  keywords: Keyword[]
}

export default function KeywordList({ keywords }: KeywordListProps) {
  if (keywords.length === 0) {
    return <p className="text-sm text-[#78847f]">No keywords identified.</p>
  }

  return (
    <ul className="divide-y divide-[#dfe7e3] border-y border-[#dfe7e3]">
      {keywords.map((keyword, index) => (
        <li
          key={`${keyword.word}-${keyword.pos}-${index}`}
          className="grid gap-1 py-3 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1.5fr)_minmax(5rem,0.5fr)] sm:gap-4"
        >
          <span className="font-medium text-[#18211f]">{keyword.word}</span>
          <span className="text-sm text-[#4f5c57]">{keyword.meaning}</span>
          <span className="text-xs text-[#6d7975] sm:text-sm">{keyword.pos}</span>
        </li>
      ))}
    </ul>
  )
}
