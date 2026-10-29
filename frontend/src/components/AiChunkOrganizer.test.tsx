import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiChunkOrganizer from './AiChunkOrganizer'


const fetchMock = vi.fn()
const candidate = {
  candidate_id: 'candidate-1', state: 'proposed', version: 1, source_language: 'en',
  text: 'as far as i know', normalized_identity: 'as far as i know',
  suggested_meaning_zh: '据我所知', usage_note: '口语表达', example: 'As far as I know, it works.',
  explanation_of_value: 'Reusable', quality_flags: ['mock_adapter'],
  evidence: { source_type: 'manual_text', quote: 'As far as I know, it works.', source_label: 'Manual learning text', evidence_start: 0, evidence_end: 16 },
  proposal_model: 'mock-chunk-organizer', proposal_version: '1',
  existing_study_item_id: null, existing_study_item_active: null, accepted_study_item_id: null,
}

function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }))
}

describe('AiChunkOrganizer', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('crypto', { randomUUID: () => '00000000-0000-4000-8000-000000000001' })
    fetchMock.mockReset()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('does not generate or save until the learner explicitly acts', async () => {
    render(<AiChunkOrganizer learnerId="learner-1" />)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Generate candidates' })).toBeDisabled()
  })

  it('shows source-grounded candidates and accepts an edited candidate once', async () => {
    const user = userEvent.setup()
    fetchMock
      .mockImplementationOnce(() => response({ batch_id: 'batch-1', user_id: 'learner-1', source_language: 'en', created_at: '2026-10-29T09:00:00Z', candidates: [candidate] }))
      .mockImplementationOnce(() => response({ candidate: { ...candidate, state: 'edited_and_accepted', version: 2, suggested_meaning_zh: '依我所知', accepted_study_item_id: 'study-1' }, study_item: null, created: true, warnings: ['Chunk Memory compatibility sync failed; Study Bank save succeeded.'] }))
    render(<AiChunkOrganizer learnerId="learner-1" />)
    await user.type(screen.getByLabelText('Learning text'), 'As far as I know, it works.')
    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))
    expect(await screen.findByDisplayValue('据我所知')).toBeInTheDocument()
    expect(screen.getByText(/Context:/)).toHaveTextContent('As far as I know, it works.')
    await user.clear(screen.getByLabelText('Chinese explanation'))
    await user.type(screen.getByLabelText('Chinese explanation'), '依我所知')
    await user.click(screen.getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(screen.getByText('edited and accepted')).toBeInTheDocument())
    expect(screen.getByRole('status')).toHaveTextContent('Study Bank save succeeded')
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body))
    expect(body.action).toBe('edit_and_accept')
    expect(body.expected_version).toBe(1)
    expect(body.user_id).toBe('learner-1')
  })

  it('rejects without sending an edit or saving silently', async () => {
    const user = userEvent.setup()
    fetchMock
      .mockImplementationOnce(() => response({ batch_id: 'batch-1', user_id: 'learner-1', source_language: 'en', created_at: '2026-10-29T09:00:00Z', candidates: [candidate] }))
      .mockImplementationOnce(() => response({ candidate: { ...candidate, state: 'rejected', version: 2 }, study_item: null, created: false }))
    render(<AiChunkOrganizer learnerId="learner-1" />)
    await user.type(screen.getByLabelText('Learning text'), 'As far as I know, it works.')
    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))
    await user.click(await screen.findByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(screen.getByText('rejected')).toBeInTheDocument())
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body))
    expect(body.action).toBe('reject')
    expect(body.edit).toBeUndefined()
  })

  it('warns that accepting an archived duplicate will not restore it', async () => {
    const user = userEvent.setup()
    fetchMock.mockImplementationOnce(() => response({
      batch_id: 'batch-1', user_id: 'learner-1', source_language: 'en', created_at: '2026-10-29T09:00:00Z',
      candidates: [{ ...candidate, existing_study_item_id: 'archived-1', existing_study_item_active: false }],
    }))
    render(<AiChunkOrganizer learnerId="learner-1" />)
    await user.type(screen.getByLabelText('Learning text'), 'As far as I know.')
    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))
    expect(await screen.findByText(/archived; accepting will not restore it/)).toBeInTheDocument()
  })
})
