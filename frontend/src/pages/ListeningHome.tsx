import { useEffect, useMemo, useState } from 'react'

import { getLibraryItem, listLibraryItems } from '../api/library'
import type { LibraryItem, LibraryItemSummary } from '../types/library'

type LanguageFilter = 'all' | 'en' | 'de'

function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

interface ListeningHomeProps {
  onOpen: (item: LibraryItem) => void
}

export default function ListeningHome({ onOpen }: ListeningHomeProps) {
  const [items, setItems] = useState<LibraryItemSummary[]>([])
  const [filter, setFilter] = useState<LanguageFilter>('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    void listLibraryItems().then((value) => {
      if (active) setItems(value)
    }).catch(() => {
      if (active) setError('Listening Library is unavailable. Please try again.')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [])

  const visible = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return items.filter((item) =>
      (filter === 'all' || item.source_language === filter) &&
      (!query || item.title.toLocaleLowerCase().includes(query) ||
        item.source_url.toLocaleLowerCase().includes(query)),
    )
  }, [filter, items, search])

  async function open(itemId: string) {
    setOpeningId(itemId)
    setError(null)
    try {
      onOpen(await getLibraryItem(itemId))
    } catch {
      setError('This saved media could not be loaded. It may have been deleted.')
    } finally {
      setOpeningId(null)
    }
  }

  return (
    <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
      <h1 className="text-3xl font-semibold">Listening</h1>
      <p className="mt-2 text-[#66726e]">Return to saved media for intensive, transcript-led listening practice.</p>
      <div className="mt-6 flex flex-wrap gap-3">
        {(['all', 'en', 'de'] as const).map((language) => (
          <button key={language} type="button" aria-pressed={filter === language} onClick={() => setFilter(language)} className="h-10 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white">
            {language === 'all' ? 'All' : language === 'en' ? 'English' : 'German'}
          </button>
        ))}
        <label className="sr-only" htmlFor="listening-search">Search Listening media</label>
        <input id="listening-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search saved media" className="h-10 min-w-64 border border-[#aebcb6] px-3" />
      </div>
      {loading && <p role="status" className="mt-8">Loading saved media…</p>}
      {error && <p role="alert" className="mt-8 text-[#8f1d14]">{error}</p>}
      {!loading && !error && items.length === 0 && <p className="mt-8 border-y border-[#ccd6d1] bg-white p-6">No saved media yet. Generate a Lesson to add it to My Library.</p>}
      {!loading && items.length > 0 && visible.length === 0 && <p className="mt-8">No saved media matches this filter.</p>}
      <ul className="mt-8 grid gap-4 md:grid-cols-2">
        {visible.map((item) => (
          <li key={item.id} className="border-y border-[#ccd6d1] bg-white p-5">
            <h2 className="text-lg font-semibold">{item.title}</h2>
            <p className="mt-2 text-sm text-[#66726e]">
              {item.source_language === 'en' ? 'English' : 'German'} · {item.level} · {item.sentence_count} sentences
              {item.duration_seconds !== null ? ` · ${formatTime(item.duration_seconds)}` : ''}
            </p>
            <p className="mt-2 text-sm">{item.audio_available ? 'Audio saved' : 'Transcript only'} · Updated {new Date(item.updated_at).toLocaleDateString()}</p>
            {item.has_listening_progress && <p className="mt-2 text-sm text-[#176b5b]">Continue at {formatTime(item.last_position_seconds)}</p>}
            <button type="button" disabled={openingId === item.id} onClick={() => void open(item.id)} className="mt-5 h-10 bg-[#176b5b] px-4 text-white disabled:bg-[#9aaba5]">
              {item.has_listening_progress ? `Continue Listening at ${formatTime(item.last_position_seconds)}` : 'Start Listening'}
            </button>
          </li>
        ))}
      </ul>
    </main>
  )
}
