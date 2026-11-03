import { act, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CloudConflictError } from '../auth/client'
import { getLibraryItem, updateLibraryProgress } from '../api/library'
import { libraryItem } from '../testFixtures/libraryItem'
import type { TrainingSessionState } from '../training/trainingMachine'
import type { LibraryItem } from '../types/library'
import { CloudGuided } from './CloudLibrary'

// Capture every LessonViewer instance's callback so a replaced view can be
// made to emit late, as a click landing during a re-render would.
const viewers: Array<(isTraining: boolean, state: TrainingSessionState) => void> = []
vi.mock('../pages/LessonViewer', () => ({
  default: (props: { onSessionChange: (isTraining: boolean, state: TrainingSessionState) => void }) => {
    viewers.push(props.onSessionChange)
    return null
  },
}))
vi.mock('../api/library', () => ({ getLibraryItem: vi.fn(), updateLibraryProgress: vi.fn(), updateListeningProgress: vi.fn() }))

afterEach(() => { viewers.length = 0; vi.mocked(updateLibraryProgress).mockReset(); vi.mocked(getLibraryItem).mockReset() })

const order = [...libraryItem.progress.training_order]
const state = (stageIndex: number): TrainingSessionState => ({
  order: [...order], stageIndex, sentenceIndex: 0, completedStages: order.slice(0, stageIndex), completedSentences: {},
})
const item = (revision: number, stageIndex: number): LibraryItem => ({
  ...libraryItem,
  revision,
  progress: { ...libraryItem.progress, is_training: true, stage_index: stageIndex, sentence_index: 0, completed_stages: order.slice(0, stageIndex), lesson_memory_imported: false },
})

describe('CloudGuided', () => {
  it('ignores a replaced view after adopting another tab’s progress', async () => {
    vi.mocked(updateLibraryProgress).mockRejectedValueOnce(new CloudConflictError())
    vi.mocked(getLibraryItem).mockResolvedValueOnce(item(5, 4))
    render(<CloudGuided item={item(1, 1)} generation={1} onBack={vi.fn()} />)
    const replaced = viewers[0]

    await act(async () => { replaced(true, state(2)) })
    await waitFor(() => expect(viewers.length).toBeGreaterThan(1))
    await act(async () => { replaced(true, state(2)) })

    expect(vi.mocked(updateLibraryProgress)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(updateLibraryProgress).mock.calls[0][2]).toEqual({ expectedRevision: 1, generation: 1 })
  })
})
