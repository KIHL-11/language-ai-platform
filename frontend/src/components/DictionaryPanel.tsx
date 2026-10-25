import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type SyntheticEvent,
} from 'react'
import { createPortal } from 'react-dom'

import { lookupDictionary, synthesizePronunciation } from '../api/dictionary'
import { calculateDictionaryPopoverPosition, type DictionaryPopoverPosition } from '../dictionary/popoverPosition'
import type { DictionaryLookup, DictionaryPronunciation, DictionarySelection } from '../types/dictionary'


interface DictionaryPanelProps {
  selection: DictionarySelection
  anchor: HTMLElement
  onClose: () => void
}

function Explanation({ value }: { value: DictionaryLookup['explanations']['monolingual'] }) {
  if (value.status === 'unavailable' || !value.value) {
    return <p className="text-sm text-[#66726e]">This source does not provide this explanation.</p>
  }
  return <>
    <p className="text-sm leading-5">{value.value}</p>
    <p className="mt-1 text-[11px] text-[#66726e]">{value.kind === 'dictionary_translation' ? 'Dictionary translation' : 'Dictionary definition'} · {value.source}</p>
  </>
}

function levelLabels(language: 'en' | 'de') {
  return language === 'de'
    ? ['01 Deutsch → Deutsch', '02 Deutsch → English', '03 Deutsch → 中文']
    : ['01 English → English', '02 English → Deutsch', '03 English → 中文']
}

function DictionaryPanelContent({ selection, anchor, onClose }: DictionaryPanelProps) {
  const [entry, setEntry] = useState<DictionaryLookup | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const [pronunciationError, setPronunciationError] = useState<string | null>(null)
  const [moreLoading, setMoreLoading] = useState(false)
  const [moreLoaded, setMoreLoaded] = useState(false)
  const [moreError, setMoreError] = useState<string | null>(null)
  const [position, setPosition] = useState<DictionaryPopoverPosition>(() =>
    calculateDictionaryPopoverPosition(anchor.getBoundingClientRect(), 0, window.innerWidth, window.innerHeight))
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const moreControllerRef = useRef<AbortController | null>(null)
  const panelRef = useRef<HTMLElement | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)
  const headingId = useId()

  const closeAndRestoreFocus = useCallback(() => {
    onClose()
    if (anchor.isConnected) anchor.focus({ preventScroll: true })
  }, [anchor, onClose])

  useEffect(() => {
    const controller = new AbortController()
    void lookupDictionary(selection, controller.signal).then(setEntry).catch((reason) => {
      if ((reason as { name?: string }).name !== 'AbortError') setError('Dictionary lookup failed. Listening is still available.')
    })
    return () => controller.abort()
  }, [selection])

  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true })
    return () => {
      const moreController = moreControllerRef.current
      moreControllerRef.current = null
      moreController?.abort()
      audioRef.current?.pause()
      audioRef.current = null
    }
  }, [])

  useLayoutEffect(() => {
    const updatePosition = () => {
      if (!anchor.isConnected) {
        onClose()
        return
      }
      setPosition(calculateDictionaryPopoverPosition(
        anchor.getBoundingClientRect(),
        panelRef.current?.getBoundingClientRect().height ?? 0,
        window.innerWidth,
        window.innerHeight,
      ))
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    const resizeObserver = typeof ResizeObserver === 'undefined' || !panelRef.current
      ? null
      : new ResizeObserver(updatePosition)
    if (panelRef.current) resizeObserver?.observe(panelRef.current)
    const mutationObserver = new MutationObserver(updatePosition)
    mutationObserver.observe(document.body, { childList: true, subtree: true })
    return () => {
      window.removeEventListener('resize', updatePosition)
      resizeObserver?.disconnect()
      mutationObserver.disconnect()
    }
  }, [anchor, onClose])

  useEffect(() => {
    const closeFromKeyboard = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      closeAndRestoreFocus()
    }
    const closeFromOutside = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (panelRef.current?.contains(target) || anchor.contains(target)) return
      closeAndRestoreFocus()
    }
    const closeFromScroll = (event: Event) => {
      const target = event.target
      if (target instanceof Node && panelRef.current?.contains(target)) return
      closeAndRestoreFocus()
    }
    document.addEventListener('keydown', closeFromKeyboard)
    document.addEventListener('pointerdown', closeFromOutside)
    document.addEventListener('scroll', closeFromScroll, true)
    return () => {
      document.removeEventListener('keydown', closeFromKeyboard)
      document.removeEventListener('pointerdown', closeFromOutside)
      document.removeEventListener('scroll', closeFromScroll, true)
    }
  }, [anchor, closeAndRestoreFocus])

  async function play(pronunciation: DictionaryPronunciation) {
    if (!entry) return
    setPronunciationError(null)
    setPlaying(pronunciation.accent)
    try {
      const audioUrl = pronunciation.tts_fallback
        ? (await synthesizePronunciation(
            entry.lemma ?? entry.surface_form,
            pronunciation.accent as 'en-US' | 'en-GB' | 'de-DE',
          )).audioUrl
        : pronunciation.audio_url
      if (!audioUrl) throw new Error('No audio source')
      audioRef.current?.pause()
      const audio = new Audio(audioUrl)
      audioRef.current = audio
      audio.addEventListener('ended', () => setPlaying(null), { once: true })
      audio.addEventListener('error', () => {
        setPlaying(null)
        setPronunciationError('Pronunciation audio is unavailable. Try again when online.')
      }, { once: true })
      await audio.play()
    } catch {
      setPlaying(null)
      setPronunciationError('Pronunciation audio is unavailable. Try again when online.')
    }
  }

  function loadInflectionsWhenOpened(event: SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open) {
      const controller = moreControllerRef.current
      moreControllerRef.current = null
      controller?.abort()
      setMoreLoading(false)
      return
    }
    if (!entry || moreLoaded || moreLoading) return
    const controller = new AbortController()
    moreControllerRef.current?.abort()
    moreControllerRef.current = controller
    setMoreLoading(true)
    setMoreError(null)
    void lookupDictionary(selection, controller.signal, true).then((detailedEntry) => {
      setEntry(detailedEntry)
      setMoreLoaded(true)
    }).catch((reason) => {
      if ((reason as { name?: string }).name !== 'AbortError') {
        setMoreError('Inflections are unavailable. Other dictionary details remain available.')
      }
    }).finally(() => {
      if (moreControllerRef.current === controller) {
        moreControllerRef.current = null
        setMoreLoading(false)
      }
    })
  }

  const labels = levelLabels(selection.language)
  return <aside
    ref={panelRef}
    role="dialog"
    aria-modal="false"
    aria-labelledby={headingId}
    aria-label="Contextual dictionary"
    data-placement={position.placement}
    style={position.style}
    className="z-[100] overflow-y-auto rounded-xl border border-[#8fa69d] bg-white p-4 shadow-[0_16px_48px_rgba(23,33,31,0.22)]"
  >
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-[10px] font-semibold tracking-wider text-[#66726e]">DICTIONARY · {selection.language === 'de' ? 'GERMAN' : 'ENGLISH'}</p>
        <h2 id={headingId} className="mt-1 truncate text-xl font-semibold">{entry?.lemma ?? selection.word}</h2>
        {entry && <p className="mt-0.5 text-xs text-[#596560]">{entry.part_of_speech ?? 'Part of speech unavailable'}{entry.lemma && entry.lemma !== entry.surface_form ? ` · ${entry.surface_form}` : ''}</p>}
      </div>
      <button ref={closeRef} type="button" onClick={closeAndRestoreFocus} aria-label="Close dictionary" className="h-8 shrink-0 rounded-md border border-[#aebcb6] px-2 text-xs">Close</button>
    </div>

    {!entry && !error && <p role="status" className="mt-3 text-sm">Looking up {selection.word}…</p>}
    {error && <p role="alert" className="mt-3 text-sm text-[#8f1d14]">{error}</p>}
    {entry?.status === 'not_configured' && <p role="status" className="mt-3 text-sm">Local dictionary data is not installed.</p>}
    {entry?.status === 'not_found' && <p role="status" className="mt-3 text-sm">No dictionary entry was found for “{selection.word}”.</p>}

    {entry && (entry.status === 'found' || entry.status === 'ambiguous') && <>
      {entry.status === 'ambiguous' && <p className="mt-3 rounded-md bg-[#fff8e8] px-3 py-2 text-xs">Ambiguous lookup. The first source sense is shown.</p>}
      <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Pronunciation">
        {entry.pronunciations.filter((value) => value.audio_available).map((value, index) => <button
          key={`${value.accent}-${index}`}
          type="button"
          disabled={playing !== null}
          onClick={() => void play(value)}
          className="min-h-8 rounded-md border border-[#176b5b] px-2 text-xs disabled:border-[#9aaba5]"
        >{playing === value.accent ? 'Playing…' : `🔊 ${value.accent}`}{value.ipa ? ` ${value.ipa}` : ''} <span className="sr-only">{value.tts_fallback ? 'TTS' : 'dictionary recording'}</span></button>)}
        {entry.pronunciations.every((value) => !value.audio_available) && <span className="text-xs text-[#66726e]">Pronunciation unavailable</span>}
      </div>
      {pronunciationError && <p role="alert" className="mt-2 text-xs text-[#8f1d14]">{pronunciationError}</p>}

      <div className="mt-3 space-y-1">
        <details open className="border-t border-[#dfe7e3] py-2"><summary className="cursor-pointer text-sm font-semibold">{labels[0]}</summary><div className="mt-1.5"><Explanation value={entry.explanations.monolingual} /></div></details>
        <details className="border-t border-[#dfe7e3] py-2"><summary className="cursor-pointer text-sm font-semibold">{labels[1]}</summary><div className="mt-1.5"><Explanation value={entry.explanations.cross_language} /></div></details>
        <details className="border-t border-[#dfe7e3] py-2"><summary className="cursor-pointer text-sm font-semibold">{labels[2]}</summary><div className="mt-1.5"><Explanation value={entry.explanations.chinese} /></div></details>
      </div>

      <details className="border-t border-[#dfe7e3] py-2" onToggle={loadInflectionsWhenOpened}>
        <summary className="cursor-pointer text-sm font-semibold">More</summary>
        <div className="mt-2 space-y-3 text-sm">
          <div><p className="text-[10px] font-semibold tracking-wider text-[#66726e]">SOURCE CONTEXT</p><p className="mt-1">{entry.context.source_sentence}</p></div>
          {entry.examples.length > 0 && <div><h3 className="font-semibold">Examples</h3><p className="mt-1">{entry.examples[0]}</p></div>}
          {moreLoading && <p role="status" className="text-xs text-[#66726e]">Loading inflections…</p>}
          {moreError && <p role="status" className="text-xs text-[#8f1d14]">{moreError}</p>}
          {entry.inflections.length > 0 && <div><h3 className="font-semibold">Inflections</h3><p className="mt-1">{entry.inflections.join(' · ')}</p></div>}
          {moreLoaded && entry.inflections.length === 0 && <p className="text-xs text-[#66726e]">
            {entry.inflections_message ?? 'Inflections unavailable from this source.'}
          </p>}
          {entry.alternate_senses.length > 0 && <div><h3 className="font-semibold">Alternative meanings</h3><ul className="mt-1 list-disc space-y-1 pl-5">{entry.alternate_senses.map((sense, index) => <li key={`${index}-${sense.definition}`}>{sense.definition}</li>)}</ul></div>}
          {entry.source_attribution.map((source) => <p key={source.source_url} className="text-xs text-[#66726e]">Source: <a href={source.source_url} target="_blank" rel="noreferrer" className="underline">{source.source}</a> · {source.license}</p>)}
        </div>
      </details>
    </>}
  </aside>
}

export default function DictionaryPanel(props: DictionaryPanelProps) {
  const { selection } = props
  return createPortal(
    <DictionaryPanelContent
      key={`${selection.language}:${selection.word}:${selection.sentence}`}
      {...props}
    />,
    document.body,
  )
}
