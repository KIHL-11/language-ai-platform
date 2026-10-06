import { useMemo, useState } from 'react'

import {
  collectReusedChunkIds,
  type DialogueChunk,
  type DialogueMessage,
} from '../../training/liveDialogue'

interface DialogueReviewProps {
  messages: DialogueMessage[]
  candidateChunks: DialogueChunk[]
  notice?: string | null
  onRestart: () => void
  onComplete: () => void
}

const SELF_REVIEW_ITEMS = [
  'I responded without copying the lesson transcript.',
  'I reused useful expressions.',
  'I asked or answered follow-up questions naturally.',
] as const

export default function DialogueReview({
  messages,
  candidateChunks,
  notice,
  onRestart,
  onComplete,
}: DialogueReviewProps) {
  const [selfReview, setSelfReview] = useState<boolean[]>(() =>
    SELF_REVIEW_ITEMS.map(() => false),
  )
  const usedChunkIds = useMemo(
    () => collectReusedChunkIds(messages, candidateChunks),
    [candidateChunks, messages],
  )
  const reusedChunks = candidateChunks.filter((chunk) => usedChunkIds.includes(chunk.id))
  const unusedChunks = candidateChunks.filter((chunk) => !usedChunkIds.includes(chunk.id))

  return (
    <div>
      <section className="border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6" aria-labelledby="dialogue-review-title">
        <h3 id="dialogue-review-title" className="text-xl font-semibold text-[#25312d]">
          Conversation review
        </h3>
        {notice && <p role="status" className="mt-3 text-sm text-[#66726e]">{notice}</p>}
        {messages.length > 0 ? (
          <ol aria-label="Conversation transcript" className="mt-5 space-y-4">
            {messages.map((message) => (
              <li key={message.id}>
                <p className="text-xs font-semibold uppercase text-[#66726e]">
                  {message.role === 'learner' ? 'You' : 'Partner'}
                </p>
                <p className="mt-1 leading-7 text-[#25312d]">{message.text}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-4 text-sm text-[#66726e]">
            No transcript was available for this conversation.
          </p>
        )}
      </section>

      <section className="mt-5 border-y border-[#c9d5d0] bg-[#f8faf9] px-5 py-6 sm:px-6" aria-labelledby="chunk-review-title">
        <h3 id="chunk-review-title" className="font-semibold text-[#25312d]">
          Useful expressions reused: {reusedChunks.length} / {candidateChunks.length}
        </h3>
        {candidateChunks.length === 0 ? (
          <p className="mt-3 text-sm text-[#66726e]">No lesson chunks were available for this conversation.</p>
        ) : (
          <div className="mt-4 grid gap-5 sm:grid-cols-2">
            <div>
              <h4 className="text-sm font-semibold text-[#176b5b]">Reused</h4>
              <ul className="mt-2 space-y-1 text-sm text-[#596560]">
                {reusedChunks.length > 0
                  ? reusedChunks.map((chunk) => <li key={chunk.id}>{chunk.text}</li>)
                  : <li>None yet</li>}
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-[#7a5b18]">Not reused</h4>
              <ul className="mt-2 space-y-1 text-sm text-[#596560]">
                {unusedChunks.map((chunk) => <li key={chunk.id}>{chunk.text}</li>)}
              </ul>
            </div>
          </div>
        )}
      </section>

      <fieldset className="mt-5 border-y border-[#c9d5d0] bg-white px-5 py-6 sm:px-6">
        <legend className="font-semibold text-[#25312d]">Self-review</legend>
        <p className="mt-1 text-sm text-[#66726e]">
          These prompts are for your reflection. The app does not verify them.
        </p>
        <div className="mt-4 space-y-3">
          {SELF_REVIEW_ITEMS.map((item, index) => (
            <label key={item} className="flex items-start gap-3 text-sm text-[#34413d]">
              <input
                type="checkbox"
                checked={selfReview[index]}
                onChange={(event) => {
                  const next = [...selfReview]
                  next[index] = event.target.checked
                  setSelfReview(next)
                }}
                className="mt-0.5 h-4 w-4 accent-[#176b5b]"
              />
              <span>{item}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mt-6 flex flex-wrap justify-between gap-3">
        <button
          type="button"
          onClick={onRestart}
          className="h-10 border border-[#aebcb6] bg-white px-4 text-sm font-semibold text-[#34413d] hover:bg-[#f3f7f5]"
        >
          Restart Dialogue
        </button>
        <button
          type="button"
          onClick={onComplete}
          className="h-10 bg-[#176b5b] px-5 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
        >
          Complete Live Dialogue
        </button>
      </div>
    </div>
  )
}
