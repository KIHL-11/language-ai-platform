import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CloudAuthError, CloudConflictError } from '../auth/client'
import { getLibraryItem, updateLibraryProgress, updateListeningProgress } from '../api/library'
import { libraryItem } from '../testFixtures/libraryItem'
import type { LibraryItem, LibraryProgress } from '../types/library'
import { createCloudProgressSync, furthestProgress, MAX_CONFLICT_RETRIES } from './cloudProgressSync'

vi.mock('../api/library', () => ({
  getLibraryItem: vi.fn(),
  updateLibraryProgress: vi.fn(),
  updateListeningProgress: vi.fn(),
}))

const order = [...libraryItem.progress.training_order]
const atStage = (stageIndex: number, sentenceIndex = 0, extra: Partial<LibraryProgress> = {}): LibraryProgress => ({
  is_training: true,
  stage_index: stageIndex,
  sentence_index: sentenceIndex,
  completed_stages: order.slice(0, stageIndex),
  training_order: [...order],
  lesson_memory_imported: false,
  ...extra,
})
const cloudItem = (revision: number, progress = atStage(1), listening = libraryItem.listening_progress): LibraryItem =>
  ({ ...libraryItem, revision, progress, listening_progress: listening })

const update = vi.mocked(updateLibraryProgress)
const updateListening = vi.mocked(updateListeningProgress)
const reload = vi.mocked(getLibraryItem)

beforeEach(() => { update.mockReset(); updateListening.mockReset(); reload.mockReset() })

describe('furthestProgress', () => {
  it('keeps the further position as a whole and never un-imports memory', () => {
    const local = atStage(2, 1, { lesson_memory_imported: false, is_training: false })
    const saved = atStage(4, 0, { lesson_memory_imported: true })
    expect(furthestProgress(local, saved)).toEqual({ ...saved, is_training: false, lesson_memory_imported: true })
    expect(furthestProgress(saved, local)).toEqual({ ...saved, lesson_memory_imported: true })
  })

  it('lets the saved state win when the lesson was replaced', () => {
    const replaced = { ...atStage(0), training_order: order.slice(0, 3), completed_stages: [] }
    expect(furthestProgress(atStage(5), replaced)).toBe(replaced)
  })
})

describe('createCloudProgressSync', () => {
  it('sends the current revision and generation, then follows the server revision', async () => {
    update.mockResolvedValueOnce(cloudItem(8, atStage(2))).mockResolvedValueOnce(cloudItem(9, atStage(3)))
    const sync = createCloudProgressSync({ item: cloudItem(7), generation: 4 })

    await sync.saveGuided(atStage(2))
    await sync.saveGuided(atStage(3))

    expect(update.mock.calls.map((call) => call[2])).toEqual([
      { expectedRevision: 7, generation: 4 },
      { expectedRevision: 8, generation: 4 },
    ])
  })

  it('skips writes that match the saved state', async () => {
    const sync = createCloudProgressSync({ item: cloudItem(3, atStage(1)), generation: 1 })
    await sync.saveGuided(atStage(1))
    await sync.saveListening(libraryItem.listening_progress)
    expect(update).not.toHaveBeenCalled()
    expect(updateListening).not.toHaveBeenCalled()
  })

  it('coalesces rapid writes to the latest snapshot', async () => {
    let finish!: (item: LibraryItem) => void
    update.mockImplementationOnce(() => new Promise((done) => { finish = done }))
      .mockResolvedValueOnce(cloudItem(3, atStage(4)))
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1 })

    void sync.saveGuided(atStage(2))
    void sync.saveGuided(atStage(3))
    const last = sync.saveGuided(atStage(4))
    finish(cloudItem(2, atStage(2)))
    await last

    expect(update.mock.calls.map((call) => call[1].stage_index)).toEqual([2, 4])
  })

  it('adopts a further saved state from another tab instead of overwriting it', async () => {
    const ahead = cloudItem(5, atStage(4, 2, { lesson_memory_imported: true }))
    update.mockRejectedValueOnce(new CloudConflictError())
    reload.mockResolvedValueOnce(ahead)
    const merged = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(2), generation: 1, onGuidedMerged: merged })

    await sync.saveGuided(atStage(2))

    expect(reload).toHaveBeenCalledWith(libraryItem.id, { generation: 1 })
    expect(merged).toHaveBeenCalledWith(ahead, { ...ahead.progress })
    expect(update).toHaveBeenCalledTimes(1)
  })

  it('retries a further local state on the reloaded revision', async () => {
    update.mockRejectedValueOnce(new CloudConflictError()).mockResolvedValueOnce(cloudItem(6, atStage(5)))
    reload.mockResolvedValueOnce(cloudItem(5, atStage(3), { ...libraryItem.listening_progress, mode: 'blind' }))
    const merged = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(2), generation: 1, onGuidedMerged: merged })

    await sync.saveGuided(atStage(5))

    expect(update.mock.calls[1]).toEqual([libraryItem.id, atStage(5), { expectedRevision: 5, generation: 1 }])
    expect(merged).not.toHaveBeenCalled()
  })

  it('reapplies the active tab listening snapshot on the reloaded revision', async () => {
    const snapshot = { ...libraryItem.listening_progress, last_position_seconds: 2.5 }
    updateListening.mockRejectedValueOnce(new CloudConflictError()).mockResolvedValueOnce(cloudItem(4, atStage(1), snapshot))
    reload.mockResolvedValueOnce(cloudItem(3, atStage(1), { ...snapshot, last_position_seconds: 0.5 }))
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 2 })

    await sync.saveListening(snapshot)

    expect(updateListening.mock.calls.map((call) => call[2])).toEqual([
      { expectedRevision: 1, generation: 2 },
      { expectedRevision: 3, generation: 2 },
    ])
  })

  it('gives up after bounded conflicts and reports it', async () => {
    update.mockRejectedValue(new CloudConflictError())
    let revision = 1
    reload.mockImplementation(async () => cloudItem(++revision, atStage(1)))
    const failure = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1, onFailure: failure })

    await sync.saveGuided(atStage(3))

    expect(update).toHaveBeenCalledTimes(MAX_CONFLICT_RETRIES + 1)
    expect(failure).toHaveBeenCalledWith('conflict')
  })

  it.each([
    ['identity change', new CloudAuthError(409, 'Identity changed')],
    ['expired session', new CloudAuthError(401, 'Cloud session unavailable')],
    ['network', new TypeError('Failed to fetch')],
  ])('drops the write on %s without reloading or retrying', async (_name, error) => {
    update.mockRejectedValueOnce(error)
    const failure = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1, onFailure: failure })

    await sync.saveGuided(atStage(2))

    expect(update).toHaveBeenCalledTimes(1)
    expect(reload).not.toHaveBeenCalled()
    expect(failure).toHaveBeenCalledWith('unavailable')
  })

  it('still writes a save that lands while the queue is finishing', async () => {
    update.mockResolvedValueOnce(cloudItem(2, atStage(2))).mockResolvedValueOnce(cloudItem(3, atStage(3)))
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1 })
    const first = sync.saveGuided(atStage(2))
    await Promise.resolve()
    await Promise.resolve()
    const second = sync.saveGuided(atStage(3))

    await Promise.all([first, second])
    await sync.idle()

    expect(update.mock.calls.map((call) => call[1].stage_index)).toEqual([2, 3])
  })

  it('refuses items without a revision', () => {
    expect(() => createCloudProgressSync({ item: libraryItem, generation: 1 })).toThrow()
  })

  it('never writes a snapshot queued before a merge (no regression after adopting another tab)', async () => {
    let reject!: (error: unknown) => void
    update.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
    const ahead = cloudItem(5, atStage(3))
    reload.mockResolvedValueOnce(ahead)
    const merged = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1, onGuidedMerged: merged })

    const first = sync.saveGuided(atStage(0))
    // The replaced view emits another stale snapshot while the write is in flight.
    void sync.saveGuided(atStage(0, 0, { is_training: false }))
    reject(new CloudConflictError())
    await first
    await sync.idle()

    expect(merged).toHaveBeenCalledTimes(1)
    // Only the in-flight write was at stage 0; everything after the conflict
    // stays at or past the other tab's stage 3.
    expect(update.mock.calls[0][1].stage_index).toBe(0)
    expect(update.mock.calls.slice(1).every((call) => call[1].stage_index >= 3)).toBe(true)
  })

  it('writes snapshots from the adopted view normally', async () => {
    update.mockRejectedValueOnce(new CloudConflictError()).mockResolvedValueOnce(cloudItem(6, atStage(4)))
    reload.mockResolvedValueOnce(cloudItem(5, atStage(3)))
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1 })

    await sync.saveGuided(atStage(0))
    await sync.saveGuided(atStage(4))

    expect(update.mock.calls[1]).toEqual([libraryItem.id, atStage(4), { expectedRevision: 5, generation: 1 }])
  })

  it('keeps a newer queued step that is ahead of the other tab (review H1)', async () => {
    let reject!: (error: unknown) => void
    update.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
      .mockResolvedValueOnce(cloudItem(6, atStage(4)))
    reload.mockResolvedValueOnce(cloudItem(5, atStage(3)))
    const merged = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1, onGuidedMerged: merged })

    const first = sync.saveGuided(atStage(2))
    void sync.saveGuided(atStage(4))
    reject(new CloudConflictError())
    await first
    await sync.idle()

    expect(update.mock.calls.map((call) => [call[1].stage_index, call[2]?.expectedRevision])).toEqual([[2, 1], [4, 5]])
    expect(merged).not.toHaveBeenCalled()
  })

  it('adopts the other tab when it is ahead of both the in-flight and queued steps', async () => {
    let reject!: (error: unknown) => void
    update.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
    reload.mockResolvedValueOnce(cloudItem(5, atStage(5)))
    const merged = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1, onGuidedMerged: merged })

    const first = sync.saveGuided(atStage(2))
    void sync.saveGuided(atStage(4))
    reject(new CloudConflictError())
    await first
    await sync.idle()

    expect(update).toHaveBeenCalledTimes(1)
    expect(merged).toHaveBeenCalledWith(expect.anything(), atStage(5))
  })

  it('never moves the listening position behind another tab after a conflict (review L1)', async () => {
    const local = { ...libraryItem.listening_progress, last_position_seconds: 1, current_sentence_id: 1, translation_visible: false }
    const other = { ...libraryItem.listening_progress, last_position_seconds: 2.75, current_sentence_id: 3, translation_visible: true }
    updateListening.mockRejectedValueOnce(new CloudConflictError()).mockResolvedValueOnce(cloudItem(4, atStage(1), other))
    reload.mockResolvedValueOnce(cloudItem(3, atStage(1), other))
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1 })

    await sync.saveListening(local)

    expect(updateListening.mock.calls[1][1]).toEqual({ ...local, last_position_seconds: 2.75, current_sentence_id: 3 })
  })

  it('writes a deliberate rewind when there is no conflict', async () => {
    const rewound = { ...libraryItem.listening_progress, last_position_seconds: 0.25, current_sentence_id: 1 }
    updateListening.mockResolvedValueOnce(cloudItem(2, atStage(1), rewound))
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1 })

    await sync.saveListening(rewound)

    expect(updateListening.mock.calls[0][1]).toEqual(rewound)
  })

  it('drops a stale snapshot the replaced view emits while the reload is in flight', async () => {
    update.mockRejectedValueOnce(new CloudConflictError())
    let finishReload!: (item: LibraryItem) => void
    reload.mockImplementationOnce(() => new Promise((done) => { finishReload = done }))
    const merged = vi.fn()
    const sync = createCloudProgressSync({ item: cloudItem(1), generation: 1, onGuidedMerged: merged })

    const first = sync.saveGuided(atStage(1, 1))
    await vi.waitFor(() => expect(reload).toHaveBeenCalled())
    // The old view is still mounted and emits a stale step during the reload.
    void sync.saveGuided(atStage(1, 2))
    finishReload(cloudItem(5, atStage(4)))
    await first
    await sync.idle()

    expect(merged).toHaveBeenCalledTimes(1)
    expect(update).toHaveBeenCalledTimes(1)
  })
})
