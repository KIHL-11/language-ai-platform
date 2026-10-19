import { useEffect, useMemo, useState } from 'react'

import { getLibraryItem, listLibraryItems } from '../api/library'
import type { LibraryItem, LibraryItemSummary } from '../types/library'


interface Props { onOpen: (item: LibraryItem) => void }

export default function ShadowingHome({ onOpen }: Props) {
  const [items, setItems] = useState<LibraryItemSummary[]>([])
  const [language, setLanguage] = useState<'all' | 'en' | 'de'>('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [opening, setOpening] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    void listLibraryItems().then((values) => { if (current) setItems(values) })
      .catch(() => { if (current) setError('Shadowing Library is unavailable. Please try again.') })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [])

  const visible = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return items.filter((item) =>
      (language === 'all' || item.source_language === language) &&
      (!query || item.title.toLocaleLowerCase().includes(query) || item.source_url.toLocaleLowerCase().includes(query)),
    )
  }, [items, language, search])

  async function open(id: string) {
    setOpening(id)
    setError(null)
    try { onOpen(await getLibraryItem(id)) }
    catch { setError('This saved media could not be opened. It may have been deleted.') }
    finally { setOpening(null) }
  }

  return <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
    <h1 className="text-3xl font-semibold">Shadowing</h1>
    <p className="mt-2 text-[#66726e]">Practice speaking in sync with saved media, one sentence at a time.</p>
    <div className="mt-6 flex flex-wrap gap-3">
      {(['all', 'en', 'de'] as const).map((value) => <button key={value} type="button" aria-pressed={language === value} onClick={() => setLanguage(value)} className="h-10 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white">{value === 'all' ? 'All' : value === 'en' ? 'English' : 'German'}</button>)}
      <label className="sr-only" htmlFor="shadowing-search">Search Shadowing media</label>
      <input id="shadowing-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search saved media" className="h-10 min-w-64 border border-[#aebcb6] px-3" />
    </div>
    {loading && <p role="status" className="mt-8">Loading saved media…</p>}
    {error && <p role="alert" className="mt-8 text-[#8f1d14]">{error}</p>}
    {!loading && !error && items.length === 0 && <p className="mt-8 border-y border-[#ccd6d1] bg-white p-6">No saved media yet. Save a Lesson to My Library first.</p>}
    {!loading && items.length > 0 && visible.length === 0 && <p className="mt-8">No saved media matches this filter.</p>}
    <ul className="mt-8 grid gap-4 md:grid-cols-2">{visible.map((item) => <li key={item.id} className="border-y border-[#ccd6d1] bg-white p-5">
      <h2 className="text-lg font-semibold">{item.title}</h2>
      <p className="mt-2 text-sm text-[#66726e]">{item.source_language === 'en' ? 'English' : 'German'} · {item.sentence_count} sentences · {item.audio_available ? 'Reference audio saved' : 'Transcript-only mode'}</p>
      <p className="mt-2 text-sm">Attempts: {item.shadowing_attempt_count} · Sync target reached: {item.shadowing_target_sentence_count}/{item.sentence_count} · Stable sync: {item.shadowing_stable_sentence_count}/{item.sentence_count}</p>
      {item.shadowing_last_practiced_at && <p className="mt-2 text-sm text-[#66726e]">Last practiced {new Date(item.shadowing_last_practiced_at).toLocaleDateString()}</p>}
      <button type="button" disabled={opening === item.id} onClick={() => void open(item.id)} className="mt-5 h-10 bg-[#176b5b] px-4 text-white disabled:bg-[#9aaba5]">{item.shadowing_attempt_count ? 'Continue Shadowing' : 'Start Shadowing'}</button>
    </li>)}</ul>
  </main>
}
