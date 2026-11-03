import { CloudConflictError } from '../auth/client'
import {
  getLibraryItem,
  updateLibraryProgress,
  updateListeningProgress,
} from '../api/library'
import type { LibraryItem, LibraryProgress, ListeningProgress } from '../types/library'

// A conflict means another tab or device saved first. Each retry re-reads the
// item, so three in a row means something keeps writing; stop and say so.
export const MAX_CONFLICT_RETRIES = 3

export type CloudSyncFailure = 'conflict' | 'unavailable'

export interface CloudProgressSyncOptions {
  item: LibraryItem
  // Session generation the view was opened under. Requests are refused once
  // the sign-in changes, so nothing is written for the wrong account.
  generation: number
  onSaved?: (item: LibraryItem) => void
  // Guided progress was merged with a further saved state; the view should
  // continue from `merged`.
  onGuidedMerged?: (item: LibraryItem, merged: LibraryProgress) => void
  onFailure?: (reason: CloudSyncFailure) => void
}

export interface CloudProgressSync {
  saveGuided: (progress: LibraryProgress) => Promise<void>
  saveListening: (progress: ListeningProgress) => Promise<void>
  // Resolves once every queued write has finished (tests, navigation).
  idle: () => Promise<void>
}

function rank(progress: LibraryProgress): number[] {
  return [progress.completed_stages.length, progress.stage_index, progress.sentence_index]
}

function compareRank(left: number[], right: number[]): number {
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index]
  }
  return 0
}

function sameOrder(left: LibraryProgress, right: LibraryProgress): boolean {
  return left.training_order.length === right.training_order.length &&
    left.training_order.every((stage, index) => stage === right.training_order[index])
}

/**
 * Merge a local Guided snapshot with the saved one after a conflict.
 * The further position wins as a whole (it is a valid canonical prefix);
 * an imported lesson memory is never un-imported. If the lesson itself was
 * replaced (different training order), the saved state wins outright.
 */
export function furthestProgress(local: LibraryProgress, saved: LibraryProgress): LibraryProgress {
  if (!sameOrder(local, saved)) return saved
  const ahead = compareRank(rank(local), rank(saved)) >= 0 ? local : saved
  return {
    ...ahead,
    completed_stages: [...ahead.completed_stages],
    training_order: [...ahead.training_order],
    is_training: local.is_training,
    lesson_memory_imported: local.lesson_memory_imported || saved.lesson_memory_imported,
  }
}

/**
 * After a Listening conflict, display settings follow the tab in use, but the
 * playback position never moves back behind another tab's further position.
 * A rewind inside one tab is not a conflict and is written as is.
 */
export function furtherListening(local: ListeningProgress, saved: ListeningProgress): ListeningProgress {
  if (saved.last_position_seconds <= local.last_position_seconds) return local
  return {
    ...local,
    last_position_seconds: saved.last_position_seconds,
    current_sentence_id: saved.current_sentence_id,
  }
}

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function createCloudProgressSync(options: CloudProgressSyncOptions): CloudProgressSync {
  const { item, generation } = options
  if (!Number.isInteger(item.revision)) throw new Error('Cloud Library items carry a revision')
  let revision = item.revision as number
  let savedGuided: LibraryProgress = item.progress
  let savedListening: ListeningProgress = item.listening_progress
  const pending: { guided?: { progress: LibraryProgress; epoch: number }; listening?: ListeningProgress } = {}
  let running: Promise<void> | null = null
  // Bumped whenever a merge moves the view to another saved state. Guided
  // snapshots queued before that describe the replaced view; writing one
  // would move the saved progress back, so they are dropped.
  let epoch = 0

  async function reload(): Promise<LibraryItem> {
    const fresh = await getLibraryItem(item.id, { generation })
    if (!Number.isInteger(fresh.revision)) throw new Error('Cloud Library items carry a revision')
    revision = fresh.revision as number
    savedGuided = fresh.progress
    savedListening = fresh.listening_progress
    return fresh
  }

  function accept(saved: LibraryItem) {
    revision = saved.revision as number
    savedGuided = saved.progress
    savedListening = saved.listening_progress
    options.onSaved?.(saved)
  }

  async function writeGuided(progress: LibraryProgress) {
    let body = progress
    for (let attempt = 0; attempt <= MAX_CONFLICT_RETRIES; attempt += 1) {
      if (sameJson(body, savedGuided)) return
      try {
        accept(await updateLibraryProgress(item.id, body, { expectedRevision: revision, generation }))
        return
      } catch (error) {
        if (!(error instanceof CloudConflictError)) throw error
      }
      // A newer step may already be queued behind the one that conflicted;
      // fold it in so the merge never discards the learner's latest step.
      const queued = pending.guided
      if (queued && queued.epoch === epoch) {
        delete pending.guided
        body = furthestProgress(queued.progress, body)
      }
      const fresh = await reload()
      const merged = furthestProgress(body, fresh.progress)
      if (!sameJson(merged, body)) {
        epoch += 1
        options.onGuidedMerged?.(fresh, merged)
      }
      body = merged
    }
    options.onFailure?.('conflict')
  }

  async function writeListening(progress: ListeningProgress) {
    let body = progress
    for (let attempt = 0; attempt <= MAX_CONFLICT_RETRIES; attempt += 1) {
      if (sameJson(body, savedListening)) return
      try {
        accept(await updateListeningProgress(item.id, body, { expectedRevision: revision, generation }))
        return
      } catch (error) {
        if (!(error instanceof CloudConflictError)) throw error
      }
      if (pending.listening) {
        body = pending.listening
        delete pending.listening
      }
      const fresh = await reload()
      body = furtherListening(body, fresh.listening_progress)
    }
    options.onFailure?.('conflict')
  }

  async function drain() {
    while (pending.guided || pending.listening) {
      const guided = pending.guided
      const listening = pending.listening
      delete pending.guided
      delete pending.listening
      try {
        if (guided && guided.epoch === epoch) await writeGuided(guided.progress)
        if (listening) await writeListening(listening)
      } catch {
        // Network failure, expired session or a changed account: the write
        // is dropped (never replayed under another identity) and reported.
        options.onFailure?.('unavailable')
      }
    }
  }

  function schedule(): Promise<void> {
    running ??= drain().finally(() => {
      running = null
      // A save that arrived after drain's last check but before this ran.
      if (pending.guided || pending.listening) void schedule()
    })
    return running
  }

  const idle = (): Promise<void> => running ? running.then(idle) : Promise.resolve()

  return {
    saveGuided(progress) { pending.guided = { progress, epoch }; return schedule().then(idle) },
    saveListening(progress) { pending.listening = progress; return schedule().then(idle) },
    idle,
  }
}
