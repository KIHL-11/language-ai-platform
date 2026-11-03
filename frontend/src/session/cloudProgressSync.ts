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
  // continue from `merged`. A caller that remounts here must also refuse
  // late saves from the view it replaced (as CloudGuided does); this sync
  // cannot tell them apart from the new view's.
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

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function createCloudProgressSync(options: CloudProgressSyncOptions): CloudProgressSync {
  const { item, generation } = options
  if (!Number.isInteger(item.revision)) throw new Error('Cloud Library items carry a revision')
  let revision = item.revision as number
  let savedGuided: LibraryProgress = item.progress
  let savedListening: ListeningProgress = item.listening_progress
  const pending: { guided?: LibraryProgress; listening?: ListeningProgress } = {}
  let running: Promise<void> | null = null

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
      const fresh = await reload()
      // The view is still live until onGuidedMerged runs, so every step it
      // queued (before or during the reload) is the learner's latest one;
      // fold it in so the merge never discards it. From here to the callback
      // nothing awaits, so no step can slip in between. Late calls from a
      // view the callback replaces are refused by that view's owner
      // (CloudGuided), not here.
      if (pending.guided) {
        body = furthestProgress(pending.guided, body)
        delete pending.guided
      }
      const merged = furthestProgress(body, fresh.progress)
      if (!sameJson(merged, body)) options.onGuidedMerged?.(fresh, merged)
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
      await reload()
      // Playback position and display settings are a resume point that
      // follows the tab in use (not learning state): resend the latest one.
      if (pending.listening) {
        body = pending.listening
        delete pending.listening
      }
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
        if (guided) await writeGuided(guided)
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
    saveGuided(progress) { pending.guided = progress; return schedule().then(idle) },
    saveListening(progress) { pending.listening = progress; return schedule().then(idle) },
    idle,
  }
}
