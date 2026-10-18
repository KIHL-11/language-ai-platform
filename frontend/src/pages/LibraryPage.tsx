import { useEffect, useMemo, useState } from 'react'

import {
  deleteLibraryItem,
  getLibraryItem,
  listLibraryItems,
  updateLibraryProgress,
} from '../api/library'
import { createTrainingState } from '../training/trainingMachine'
import StudyCuration from '../components/StudyCuration'
import type { LibraryItem, LibraryItemSummary } from '../types/library'


interface LibraryPageProps {
  onOpen: (item: LibraryItem, startTraining: boolean) => void
}

type LanguageFilter = 'all' | 'en' | 'de'

const PROGRESS_LABELS = {
  not_started: 'Not started',
  in_progress: 'In progress',
  complete: 'Complete',
}

export default function LibraryPage({ onOpen }: LibraryPageProps) {
  const [items, setItems] = useState<LibraryItemSummary[]>([])
  const [selected, setSelected] = useState<LibraryItem | null>(null)
  const [filter, setFilter] = useState<LanguageFilter>('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [transcriptVisible, setTranscriptVisible] = useState(false)
  const [curating, setCurating] = useState(false)

  useEffect(() => {
    let active = true
    void listLibraryItems().then((result) => {
      if (active) setItems(result)
    }).catch(() => {
      if (active) setError('My Library is unavailable. Please try again.')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [])

  const visibleItems = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return items.filter((item) =>
      (filter === 'all' || item.source_language === filter) &&
      (!query || item.title.toLocaleLowerCase().includes(query) ||
        item.source_url.toLocaleLowerCase().includes(query)),
    )
  }, [filter, items, search])

  async function openDetail(itemId: string) {
    setError(null)
    try {
      setSelected(await getLibraryItem(itemId))
      setTranscriptVisible(false)
    } catch {
      setError('This Library Lesson could not be loaded. It may have been deleted.')
    }
  }

  async function trainAgain() {
    if (!selected) return
    const state = createTrainingState(selected.lesson.training_order)
    const progress = {
      is_training: true,
      stage_index: state.stageIndex,
      sentence_index: state.sentenceIndex,
      completed_stages: state.completedStages,
      training_order: state.order,
      lesson_memory_imported: selected.progress.lesson_memory_imported,
    }
    try {
      const updated = await updateLibraryProgress(selected.id, progress)
      onOpen(updated, true)
    } catch {
      setError('Training progress could not be reset. Please try again.')
    }
  }

  async function removeSelected() {
    if (!selected || !window.confirm(
      'Remove this lesson from My Library?\nYour Chunk Memory will not be deleted.',
    )) return
    try {
      await deleteLibraryItem(selected.id)
      setItems((current) => current.filter((item) => item.id !== selected.id))
      setSelected(null)
    } catch {
      setError('The Library Lesson could not be removed. Please try again.')
    }
  }

  if (selected) {
    if (curating) {
      return <main className="mx-auto max-w-5xl px-5 py-10 sm:px-8"><StudyCuration item={selected} onBack={() => setCurating(false)} /></main>
    }
    return (
      <main className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <button type="button" onClick={() => setSelected(null)} className="text-sm font-semibold text-[#176b5b]">
          Back to My Library
        </button>
        <h1 className="mt-5 text-3xl font-semibold">{selected.lesson.title}</h1>
        <p className="mt-2 text-sm text-[#66726e]">
          {selected.lesson.source_language === 'en' ? 'English' : 'German'} · {selected.lesson.level} · {selected.lesson.sentences.length} sentences
        </p>
        {selected.persistent_audio.status === 'unavailable' && (
          <p role="status" className="mt-5 border-l-4 border-[#b7791f] bg-white px-4 py-3">
            Persistent training audio is unavailable. Transcript-based practice remains available.
          </p>
        )}
        {error && <p role="alert" className="mt-5 text-[#8f1d14]">{error}</p>}
        <div className="mt-7 flex flex-wrap gap-3">
          <button type="button" onClick={() => onOpen(selected, false)} className="h-10 border border-[#176b5b] px-4">Open Lesson</button>
          <button type="button" onClick={() => onOpen(selected, true)} className="h-10 bg-[#176b5b] px-4 text-white">Continue Training</button>
          <button type="button" onClick={trainAgain} className="h-10 border border-[#176b5b] px-4">Train Again</button>
          <button type="button" onClick={() => setTranscriptVisible((value) => !value)} className="h-10 border border-[#aebcb6] px-4">View Transcript</button>
          <button type="button" onClick={() => setCurating(true)} className="h-10 border border-[#176b5b] px-4">Curate Words & Chunks</button>
          <a href={selected.lesson.source_url} target="_blank" rel="noreferrer" className="inline-flex h-10 items-center border border-[#aebcb6] px-4">Open original source</a>
          <button type="button" onClick={removeSelected} className="h-10 border border-[#b42318] px-4 text-[#b42318]">Delete from Library</button>
        </div>
        {transcriptVisible && (
          <section className="mt-10" aria-labelledby="library-transcript-title">
            <h2 id="library-transcript-title" className="text-xl font-semibold">Transcript</h2>
            <ol className="mt-4 space-y-4">
              {selected.lesson.sentences.map((sentence) => (
                <li key={sentence.id} className="border-y border-[#ccd6d1] bg-white p-4">
                  <p className="text-xs text-[#66726e]">{sentence.start.toFixed(1)}–{sentence.end.toFixed(1)}s</p>
                  <p className="mt-1 font-medium">{sentence.text}</p>
                  <p className="mt-1 text-[#596560]">{sentence.translation}</p>
                </li>
              ))}
            </ol>
          </section>
        )}
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
      <h1 className="text-3xl font-semibold">My Library</h1>
      <div className="mt-6 flex flex-wrap gap-3">
        {(['all', 'en', 'de'] as const).map((language) => (
          <button key={language} type="button" aria-pressed={filter === language} onClick={() => setFilter(language)} className="h-10 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white">
            {language === 'all' ? 'All' : language === 'en' ? 'English' : 'German'}
          </button>
        ))}
        <label className="sr-only" htmlFor="library-search">Search My Library</label>
        <input id="library-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search lessons" className="h-10 min-w-64 border border-[#aebcb6] px-3" />
      </div>
      {loading && <p role="status" className="mt-8">Loading My Library…</p>}
      {error && <p role="alert" className="mt-8 text-[#8f1d14]">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="mt-8 border-y border-[#ccd6d1] bg-white p-6">Your Library is empty. Generate a Lesson to save it here automatically.</p>
      )}
      {!loading && items.length > 0 && visibleItems.length === 0 && (
        <p className="mt-8">No Library Lessons match this filter.</p>
      )}
      <ul className="mt-8 space-y-4">
        {visibleItems.map((item) => (
          <li key={item.id} className="border-y border-[#ccd6d1] bg-white p-5">
            <button type="button" onClick={() => void openDetail(item.id)} className="w-full text-left">
              <span className="block text-lg font-semibold">{item.title}</span>
              <span className="mt-2 block text-sm text-[#66726e]">
                {item.source_language === 'en' ? 'English' : 'German'} · {item.level} · {item.sentence_count} sentences
                {item.duration_seconds !== null ? ` · ${Math.round(item.duration_seconds)}s` : ''}
              </span>
              <span className="mt-2 block text-sm">
                {PROGRESS_LABELS[item.progress_status]} · {item.audio_available ? 'Audio saved' : 'Audio unavailable'} · Updated {new Date(item.updated_at).toLocaleDateString()}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  )
}
