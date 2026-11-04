import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CloudConflictError } from '../auth/client'
import {
  decideChunkSuggestion,
  generateChunkSuggestions,
  generateLibraryChunkSuggestions,
  getChunkSuggestions,
} from '../api/aiChunks'
import type { AiChunkBatch, AiChunkCandidate } from '../types/aiChunks'
import AiChunkOrganizer from './AiChunkOrganizer'

vi.mock('../api/aiChunks', () => ({
  decideChunkSuggestion: vi.fn(),
  generateChunkSuggestions: vi.fn(),
  generateLibraryChunkSuggestions: vi.fn(),
  getChunkSuggestions: vi.fn(),
  newIdempotencyKey: vi.fn(() => crypto.randomUUID().replaceAll('-', '')),
}))

const candidate = (id: string, text: string, extra: Partial<AiChunkCandidate> = {}): AiChunkCandidate => ({
  candidate_id: id, state: 'proposed', version: 1, source_language: 'en', text, normalized_identity: text,
  suggested_meaning_zh: `${text} 释义`, usage_note: '', example: '', explanation_of_value: '', quality_flags: [],
  proposal_model: 'mock', proposal_version: '1', existing_study_item_id: null, existing_study_item_active: null,
  accepted_study_item_id: null,
  evidence: { source_type: 'manual_text', quote: `${text} here.`, source_label: 'Manual learning text', evidence_start: 0, evidence_end: text.length },
  ...extra,
})
const batch = (candidates: AiChunkCandidate[], extra: Partial<AiChunkBatch> = {}): AiChunkBatch => ({
  batch_id: 'batch-1', user_id: 'owner-a', source_language: 'en', source_type: 'manual_text',
  source_label: 'Manual learning text', created_at: '2026-11-04T00:00:00Z', expires_at: null, status: 'ready',
  candidates, ...extra,
})

beforeEach(() => { vi.mocked(decideChunkSuggestion).mockReset(); vi.mocked(getChunkSuggestions).mockReset() })

describe('AiChunkOrganizer', () => {
  it('keeps the Personal/manual-text actions unchanged: no save-only edit', async () => {
    vi.mocked(generateChunkSuggestions).mockResolvedValueOnce(batch([candidate('a', 'take part')]))
    const user = userEvent.setup()
    render(<AiChunkOrganizer learnerId="local-learner" />)
    await user.type(screen.getByLabelText('Learning text'), 'take part here.')

    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))

    expect(await screen.findByRole('button', { name: 'Accept' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save edits' })).not.toBeInTheDocument()
    expect(generateLibraryChunkSuggestions).not.toHaveBeenCalled()
  })

  it('a conflict reload keeps unsaved edits on candidates that did not change (review M2)', async () => {
    const first = candidate('a', 'take part')
    const second = candidate('b', 'make sense')
    vi.mocked(generateLibraryChunkSuggestions).mockResolvedValueOnce(batch([first, second], { source_type: 'library_passage' }))
    vi.mocked(decideChunkSuggestion).mockRejectedValueOnce(new CloudConflictError())
    vi.mocked(getChunkSuggestions).mockResolvedValueOnce(batch([{ ...first, state: 'rejected', version: 2 }, second], { source_type: 'library_passage' }))
    const user = userEvent.setup()
    render(<AiChunkOrganizer learnerId="owner-a" librarySource={{ libraryItemId: 'item-1', title: 'Lesson', language: 'en', sentenceIds: [1, 2] }} />)
    await user.click(screen.getByRole('button', { name: 'Find chunks in this lesson' }))
    const meaningB = (await screen.findAllByLabelText('Chinese explanation'))[1]
    await user.clear(meaningB)
    await user.type(meaningB, '讲得通（未保存）')

    await user.click(screen.getAllByRole('button', { name: 'Accept' })[0])

    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere')
    expect(screen.getByText('rejected')).toBeInTheDocument()
    expect(screen.getByLabelText('Chinese explanation')).toHaveValue('讲得通（未保存）')
  })

  it('says the batch expired instead of "changed elsewhere"', async () => {
    const first = candidate('a', 'take part')
    vi.mocked(generateLibraryChunkSuggestions).mockResolvedValueOnce(batch([first], { source_type: 'library_passage' }))
    vi.mocked(decideChunkSuggestion).mockRejectedValueOnce(new CloudConflictError())
    vi.mocked(getChunkSuggestions).mockResolvedValueOnce(batch([{ ...first, state: 'expired' }], { status: 'expired', source_type: 'library_passage' }))
    const user = userEvent.setup()
    render(<AiChunkOrganizer learnerId="owner-a" librarySource={{ libraryItemId: 'item-1', title: 'Lesson', language: 'en', sentenceIds: [1] }} />)
    await user.click(screen.getByRole('button', { name: 'Find chunks in this lesson' }))

    await user.click(await screen.findByRole('button', { name: 'Accept' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('expired'))
  })

  it('retries a failed generation with the same key, then uses a new key (re-review)', async () => {
    vi.mocked(generateLibraryChunkSuggestions)
      .mockRejectedValueOnce(new TypeError('response lost'))
      .mockResolvedValueOnce(batch([candidate('a', 'take part')], { source_type: 'library_passage' }))
      .mockResolvedValueOnce(batch([candidate('a', 'take part')], { batch_id: 'batch-2', source_type: 'library_passage' }))
    const user = userEvent.setup()
    render(<AiChunkOrganizer learnerId="owner-a" librarySource={{ libraryItemId: 'item-1', title: 'Lesson', language: 'en', sentenceIds: [1] }} />)
    const find = screen.getByRole('button', { name: 'Find chunks in this lesson' })

    await user.click(find)
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be generated')
    await user.click(find)
    await screen.findByRole('button', { name: 'Accept' })
    await user.click(find)
    await waitFor(() => expect(generateLibraryChunkSuggestions).toHaveBeenCalledTimes(3))

    const keys = vi.mocked(generateLibraryChunkSuggestions).mock.calls.map((call) => call[4])
    expect(keys[0]).toBe(keys[1])
    expect(keys[2]).not.toBe(keys[1])
    expect(keys.every((value) => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(value!))).toBe(true)
  })
})
