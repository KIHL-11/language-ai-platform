import { useCallback, useEffect, useRef, useState } from 'react'

import { cloudUnavailableMemoryClient } from '../api/chunkMemory'
import { getLibraryItem, listLibraryItems } from '../api/library'
import LessonViewer from '../pages/LessonViewer'
import ListeningWorkspace from '../pages/ListeningWorkspace'
import {
  createCloudProgressSync,
  type CloudProgressSync,
  type CloudSyncFailure,
} from '../session/cloudProgressSync'
import type { TrainingSessionState } from '../training/trainingMachine'
import type { LibraryItem, LibraryItemSummary, LibraryProgress, ListeningProgress } from '../types/library'

// Cloud Library learning: the owner's saved lessons, opened for Guided
// training or Listening. Everything is fetched under the session generation
// the workspace was mounted for and kept in memory only: no localStorage,
// so nothing survives into another account's view.

type View =
  | { kind: 'list' }
  | { kind: 'detail'; item: LibraryItem }
  | { kind: 'guided'; item: LibraryItem }
  | { kind: 'listening'; item: LibraryItem }

const FAILURE_MESSAGES: Record<CloudSyncFailure, string> = {
  conflict: 'This lesson keeps changing in another tab or device. Your latest step was not saved; go back and reopen it.',
  unavailable: 'Progress could not be saved. Check your connection or session, then reopen the lesson.',
}

function trainingStateFrom(progress: LibraryProgress): TrainingSessionState {
  return {
    order: [...progress.training_order],
    stageIndex: progress.stage_index,
    sentenceIndex: progress.sentence_index,
    completedStages: [...progress.completed_stages],
    completedSentences: {},
  }
}

function progressFrom(
  isTraining: boolean,
  state: TrainingSessionState,
  lessonMemoryImported: boolean,
): LibraryProgress {
  return {
    is_training: isTraining,
    stage_index: state.stageIndex,
    sentence_index: state.sentenceIndex,
    completed_stages: [...state.completedStages],
    training_order: [...state.order],
    lesson_memory_imported: lessonMemoryImported,
  }
}

function progressLabel(item: LibraryItem): string {
  const { completed_stages: done, training_order: order } = item.progress
  if (done.length === 0 && item.progress.stage_index === 0) return 'Guided training not started'
  if (done.length === order.length) return 'Guided training complete'
  return `Guided training: stage ${item.progress.stage_index + 1} of ${order.length}`
}

// One sync per opened view, created on first use (never during render).
// Callbacks go through refs so React StrictMode's double effects never
// silence a live view or deliver to an unmounted one.
function useCloudSync(
  item: LibraryItem,
  generation: number,
  handlers: {
    onGuidedMerged?: (item: LibraryItem, merged: LibraryProgress) => void
    onFailure: (reason: CloudSyncFailure) => void
  },
): () => CloudProgressSync {
  const mounted = useRef(false)
  const latest = useRef(handlers)
  const sync = useRef<CloudProgressSync | null>(null)
  useEffect(() => { latest.current = handlers })
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  return useCallback(() => {
    sync.current ??= createCloudProgressSync({
      item,
      generation,
      onGuidedMerged: (fresh, merged) => { if (mounted.current) latest.current.onGuidedMerged?.(fresh, merged) },
      onFailure: (reason) => { if (mounted.current) latest.current.onFailure(reason) },
    })
    return sync.current
  }, [item, generation])
}

export function CloudGuided({ item, generation, onBack }: { item: LibraryItem; generation: number; onBack: () => void }) {
  const [view, setView] = useState({ progress: item.progress, revision: 0 })
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<CloudSyncFailure | null>(null)
  const lessonMemoryImported = useRef(item.progress.lesson_memory_imported)
  const activeView = useRef(0)
  const getSync = useCloudSync(item, generation, {
    onGuidedMerged: (_fresh, merged) => {
      lessonMemoryImported.current = merged.lesson_memory_imported
      activeView.current += 1
      setView({ progress: merged, revision: activeView.current })
      setNotice('This lesson moved ahead in another tab or device. Continuing from the furthest point.')
    },
    onFailure: setFailure,
  })
  // Bound to one rendered view: a replaced view's late callbacks are ignored.
  const handleSessionChange = useCallback((isTraining: boolean, state: TrainingSessionState) => {
    if (view.revision !== activeView.current) return
    void getSync().saveGuided(progressFrom(isTraining, state, lessonMemoryImported.current))
  }, [getSync, view.revision])

  return <section aria-labelledby="cloud-guided-title" className="mx-auto max-w-5xl px-5 py-6">
    <button type="button" onClick={onBack} className="h-10 border border-[#aebcb6] px-4">Back to lesson</button>
    <h2 id="cloud-guided-title" className="mt-4 text-2xl font-semibold">Guided training</h2>
    <p className="mt-1 text-sm text-[#596560]">Text practice only. Voice dialogue and saved-language recall are not available in cloud yet.</p>
    {notice && <p role="status" className="mt-3 border-l-4 border-[#176b5b] bg-white px-4 py-2 text-sm">{notice}</p>}
    {failure && <p role="alert" className="mt-3 border-l-4 border-[#b42318] bg-white px-4 py-2 text-sm text-[#8f1d14]">{FAILURE_MESSAGES[failure]}</p>}
    <LessonViewer
      key={view.revision}
      lesson={item.lesson}
      initialIsTraining={view.progress.is_training}
      initialTrainingState={trainingStateFrom(view.progress)}
      onSessionChange={handleSessionChange}
      memoryClient={cloudUnavailableMemoryClient}
      voiceEnabled={false}
    />
  </section>
}

function CloudListening({ item, generation, onBack }: { item: LibraryItem; generation: number; onBack: () => void }) {
  const [failure, setFailure] = useState<CloudSyncFailure | null>(null)
  const getSync = useCloudSync(item, generation, { onFailure: setFailure })
  // Stable: ListeningWorkspace flushes on every change of this function.
  const saveProgress = useCallback((progress: ListeningProgress) => getSync().saveListening(progress), [getSync])
  return <>
    {failure && <p role="alert" className="mx-auto mt-4 max-w-5xl px-5 text-sm text-[#8f1d14]">{FAILURE_MESSAGES[failure]}</p>}
    <ListeningWorkspace item={item} onBack={onBack} saveProgress={saveProgress} studyFeatures={false} />
  </>
}

export default function CloudLibrary({ generation }: { generation: number }) {
  const [items, setItems] = useState<LibraryItemSummary[]>([])
  const [view, setView] = useState<View>({ kind: 'list' })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const opening = useRef(0)

  useEffect(() => {
    let active = true
    void listLibraryItems().then((values) => { if (active) setItems(values) })
      .catch(() => { if (active) setError('Library unavailable.') })
    return () => { active = false }
  }, [])

  // Always reload before showing a lesson, so the detail and the revision a
  // view starts from are the server's, never a cached copy.
  const open = useCallback(async (itemId: string, kind: 'detail' | 'guided' | 'listening') => {
    const ticket = ++opening.current
    setLoading(itemId)
    setError(null)
    try {
      const item = await getLibraryItem(itemId, { generation })
      if (ticket === opening.current) setView({ kind, item })
    } catch {
      if (ticket === opening.current) setError('That lesson could not be opened. It may have been removed or your session changed.')
    } finally {
      if (ticket === opening.current) setLoading(null)
    }
  }, [generation])

  useEffect(() => () => { opening.current += 1 }, [])

  if (view.kind === 'guided') {
    return <CloudGuided key={view.item.id} item={view.item} generation={generation} onBack={() => void open(view.item.id, 'detail')} />
  }
  if (view.kind === 'listening') {
    return <CloudListening key={view.item.id} item={view.item} generation={generation} onBack={() => void open(view.item.id, 'detail')} />
  }

  return <main className="mx-auto max-w-5xl px-5 py-6">
    <h2 className="text-2xl font-semibold">My Library</h2>
    {error && <p role="alert" className="mt-3 text-[#8f1d14]">{error}</p>}
    {view.kind === 'detail' ? <article aria-labelledby="cloud-lesson-title" className="mt-4 border-y border-[#ccd6d1] bg-white p-5">
      <button type="button" onClick={() => setView({ kind: 'list' })} className="h-9 border border-[#aebcb6] px-3">All lessons</button>
      <h3 id="cloud-lesson-title" className="mt-4 text-xl font-semibold">{view.item.lesson.title}</h3>
      <p className="mt-1 text-sm text-[#596560]">{view.item.lesson.sentences.length} sentences · {progressLabel(view.item)}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={loading !== null} onClick={() => void open(view.item.id, 'guided')} className="h-10 bg-[#176b5b] px-4 text-white disabled:bg-[#9aaba5]">{view.item.progress.stage_index > 0 || view.item.progress.completed_stages.length > 0 ? 'Continue guided training' : 'Start guided training'}</button>
        <button type="button" disabled={loading !== null} onClick={() => void open(view.item.id, 'listening')} className="h-10 border border-[#176b5b] px-4">Open listening</button>
      </div>
      <ol className="mt-5 space-y-1 text-sm">{view.item.lesson.sentences.map((sentence) => <li key={sentence.id}>{sentence.text}</li>)}</ol>
    </article> : <ul className="mt-4 space-y-2">
      {!error && items.length === 0 && <li className="text-sm text-[#596560]">No saved lessons yet.</li>}
      {items.map((item) => <li key={item.id}><button type="button" disabled={loading !== null} onClick={() => void open(item.id, 'detail')} className="text-left font-medium text-[#176b5b] hover:underline">{item.title}</button> <span className="text-xs text-[#596560]">{item.progress_status.replace('_', ' ')}</span></li>)}
    </ul>}
  </main>
}
