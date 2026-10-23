import { useEffect, useRef, useState } from 'react'

import { lookupDictionary, synthesizePronunciation } from '../api/dictionary'
import type { DictionaryLookup, DictionaryPronunciation, DictionarySelection } from '../types/dictionary'


interface DictionaryPanelProps {
  selection: DictionarySelection
  onClose: () => void
}

function Explanation({ value }: { value: DictionaryLookup['explanations']['monolingual'] }) {
  if (value.status === 'unavailable' || !value.value) {
    return <p className="text-sm text-[#66726e]">This source does not provide this explanation.</p>
  }
  return <>
    <p>{value.value}</p>
    <p className="mt-2 text-xs text-[#66726e]">{value.kind === 'dictionary_translation' ? 'Dictionary translation' : 'Dictionary definition'} · {value.source}</p>
  </>
}

function levelLabels(language: 'en' | 'de') {
  return language === 'de'
    ? ['01 Deutsch → Deutsch', '02 Deutsch → English', '03 Deutsch → 中文']
    : ['01 English → English', '02 English → Deutsch', '03 English → 中文']
}

function DictionaryPanelContent({ selection, onClose }: DictionaryPanelProps) {
  const [entry, setEntry] = useState<DictionaryLookup | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const [pronunciationError, setPronunciationError] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void lookupDictionary(selection, controller.signal).then(setEntry).catch((reason) => {
      if ((reason as { name?: string }).name !== 'AbortError') setError('Dictionary lookup failed. Listening is still available.')
    })
    return () => controller.abort()
  }, [selection])

  useEffect(() => () => {
    audioRef.current?.pause()
    audioRef.current = null
  }, [])

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

  const labels = levelLabels(selection.language)
  return <aside aria-label="Contextual dictionary" className="mt-5 border-y border-[#8fa69d] bg-white p-5">
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-xs font-semibold tracking-wider text-[#66726e]">CONTEXTUAL DICTIONARY</p>
        <h2 className="mt-1 text-2xl font-semibold">{entry?.lemma ?? selection.word}</h2>
        {entry && <p className="mt-1 text-sm text-[#596560]">{entry.language === 'de' ? 'German' : 'English'}{entry.part_of_speech ? ` · ${entry.part_of_speech}` : ''}{entry.lemma && entry.lemma !== entry.surface_form ? ` · selected form: ${entry.surface_form}` : ''}</p>}
      </div>
      <button type="button" onClick={onClose} aria-label="Close dictionary" className="h-9 border border-[#aebcb6] px-3">Close</button>
    </div>

    {!entry && !error && <p role="status" className="mt-4">Looking up {selection.word}…</p>}
    {error && <p role="alert" className="mt-4 text-[#8f1d14]">{error}</p>}
    {entry?.status === 'not_configured' && <p role="status" className="mt-4">Local dictionary data is not installed. Import a Kaikki/Wiktextract index to enable lookup.</p>}
    {entry?.status === 'not_found' && <p role="status" className="mt-4">No dictionary entry was found for “{selection.word}”.</p>}

    {entry && (entry.status === 'found' || entry.status === 'ambiguous') && <>
      {entry.status === 'ambiguous' && <p className="mt-4 border-l-4 border-[#b7791f] bg-[#fff8e8] px-3 py-2 text-sm">This lookup is ambiguous. The first source sense is shown; review More meanings.</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        {entry.pronunciations.filter((value) => value.audio_available).map((value, index) => <button
          key={`${value.accent}-${index}`}
          type="button"
          disabled={playing !== null}
          onClick={() => void play(value)}
          className="min-h-9 border border-[#176b5b] px-3 disabled:border-[#9aaba5]"
        >{playing === value.accent ? 'Playing…' : `🔊 ${value.accent}`} {value.ipa ? value.ipa : ''}<small className="ml-1">({value.tts_fallback ? 'TTS' : 'dictionary recording'})</small></button>)}
      </div>
      {pronunciationError && <p role="alert" className="mt-2 text-sm text-[#8f1d14]">{pronunciationError}</p>}

      <div className="mt-5 border-t border-[#dfe7e3] pt-4">
        <p className="text-xs font-semibold tracking-wider text-[#66726e]">SOURCE CONTEXT</p>
        <p className="mt-1">{entry.context.source_sentence}</p>
        {entry.context.selection_method === 'ambiguous' && <p className="mt-1 text-xs text-[#66726e]">The source data does not support a certain contextual sense selection.</p>}
      </div>

      <div className="mt-4 space-y-2">
        <details open className="border-t border-[#dfe7e3] py-3"><summary className="cursor-pointer font-semibold">{labels[0]}</summary><div className="mt-2"><Explanation value={entry.explanations.monolingual} /></div></details>
        <details className="border-t border-[#dfe7e3] py-3"><summary className="cursor-pointer font-semibold">{labels[1]}</summary><div className="mt-2"><Explanation value={entry.explanations.cross_language} /></div></details>
        <details className="border-y border-[#dfe7e3] py-3"><summary className="cursor-pointer font-semibold">{labels[2]}</summary><div className="mt-2"><Explanation value={entry.explanations.chinese} /></div></details>
      </div>

      {entry.examples.length > 0 && <div className="mt-4"><h3 className="font-semibold">Example</h3><p className="mt-1">{entry.examples[0]}</p></div>}
      {entry.inflections.length > 0 && <details className="mt-4"><summary className="cursor-pointer font-semibold">Inflections</summary><p className="mt-2 text-sm">{entry.inflections.join(' · ')}</p></details>}
      {entry.alternate_senses.length > 0 && <details className="mt-4"><summary className="cursor-pointer font-semibold">More meanings</summary><ul className="mt-2 list-disc space-y-1 pl-5">{entry.alternate_senses.map((sense, index) => <li key={`${index}-${sense.definition}`}>{sense.definition}</li>)}</ul></details>}
      <div className="mt-5 text-xs text-[#66726e]">
        {entry.source_attribution.map((source) => <p key={source.source_url}>Source: <a href={source.source_url} target="_blank" rel="noreferrer" className="underline">{source.source}</a> · {source.license}</p>)}
      </div>
    </>}
  </aside>
}

export default function DictionaryPanel(props: DictionaryPanelProps) {
  const selection = props.selection
  return <DictionaryPanelContent
    key={`${selection.language}:${selection.word}:${selection.sentence}`}
    {...props}
  />
}
