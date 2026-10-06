import type { Chunk } from '../types/lesson'

interface ChunkListProps {
  chunks: Chunk[]
}

export default function ChunkList({ chunks }: ChunkListProps) {
  if (chunks.length === 0) {
    return <p className="text-sm text-[#78847f]">No chunks identified.</p>
  }

  return (
    <ul className="divide-y divide-[#dfe7e3] border-y border-[#dfe7e3]">
      {chunks.map((chunk, index) => (
        <li key={`${chunk.text}-${index}`} className="py-3">
          <div className="grid gap-1 sm:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)] sm:gap-4">
            <span className="font-medium text-[#18211f]">{chunk.text}</span>
            <span className="text-sm text-[#4f5c57]">{chunk.meaning}</span>
          </div>
          {chunk.usage && (
            <p className="mt-2 border-l-2 border-[#a8bdb5] pl-3 text-sm leading-6 text-[#65716d]">
              {chunk.usage}
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}
