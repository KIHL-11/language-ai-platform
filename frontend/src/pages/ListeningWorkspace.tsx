import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'

import { updateListeningProgress } from '../api/library'
import { candidateInput, listStudyCandidates, saveStudyItems } from '../api/studyBank'
import { createListeningController, type ListeningControllerFactory, type ListeningMediaKind } from '../listening/createController'
import type { MediaController } from '../listening/mediaController'
import { activeSentenceIndex, validCustomLoop, type ListeningLoop } from '../listening/timeline'
import { getOrCreateLearnerId } from '../session/learnerSession'
import { compareDictation, type DictationResult } from '../training/dictation'
import type { LibraryItem, ListeningMode, ListeningPlaybackRate, ListeningProgress } from '../types/library'
import type { StudyCandidate } from '../types/studyBank'

const CLOCK_INTERVAL_MS = 200
const PERSIST_INTERVAL_SECONDS = 5
const PLAYBACK_RATES: ListeningPlaybackRate[] = [0.5, 0.75, 1, 1.25]

function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement || (target instanceof HTMLElement && target.isContentEditable)
}

interface ListeningWorkspaceProps {
  item: LibraryItem
  onBack: () => void
  controllerFactory?: ListeningControllerFactory
}

export default function ListeningWorkspace({
  item,
  onBack,
  controllerFactory = createListeningController,
}: ListeningWorkspaceProps) {
  const restoredIndex = item.listening_progress.current_sentence_id === null
    ? 0
    : Math.max(0, item.lesson.sentences.findIndex(
      (sentence) => sentence.id === item.listening_progress.current_sentence_id,
    ))
  const [controller, setController] = useState<MediaController | null>(null)
  const [mediaKind, setMediaKind] = useState<ListeningMediaKind>('transcript')
  const [mediaMessage, setMediaMessage] = useState<string | null>(null)
  const [mediaLoading, setMediaLoading] = useState(true)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(item.listening_progress.last_position_seconds)
  const [currentIndex, setCurrentIndex] = useState(restoredIndex)
  const [playbackRate, setPlaybackRate] = useState<ListeningPlaybackRate>(item.listening_progress.playback_rate)
  const [sourceVisible, setSourceVisible] = useState(item.listening_progress.source_visible)
  const [translationVisible, setTranslationVisible] = useState(item.listening_progress.translation_visible)
  const [mode, setMode] = useState<ListeningMode>(item.listening_progress.mode)
  const [blindRevealed, setBlindRevealed] = useState(false)
  const [autoFollow, setAutoFollow] = useState(true)
  const [loop, setLoop] = useState<ListeningLoop>({ type: 'none' })
  const [customA, setCustomA] = useState<number | null>(null)
  const [loopError, setLoopError] = useState<string | null>(null)
  const [dictationAnswer, setDictationAnswer] = useState('')
  const [dictationResult, setDictationResult] = useState<DictationResult | null>(null)
  const [candidates, setCandidates] = useState<StudyCandidate[]>([])
  const [candidateError, setCandidateError] = useState<string | null>(null)
  const [savingCandidate, setSavingCandidate] = useState<string | null>(null)
  const [learnerId] = useState(getOrCreateLearnerId)
  const mediaHostRef = useRef<HTMLDivElement>(null)
  const transcriptRows = useRef(new Map<number, HTMLButtonElement>())
  const currentIndexRef = useRef(currentIndex)
  const isPlayingRef = useRef(isPlaying)
  const loopRef = useRef(loop)
  const progressRef = useRef<ListeningProgress>(item.listening_progress)
  const mountedRef = useRef(true)
  const lastQueuedPosition = useRef(item.listening_progress.last_position_seconds)
  const writeTail = useRef<Promise<void>>(Promise.resolve())
  const currentSentence = item.lesson.sentences[currentIndex] ?? null
  const lessonDuration = Math.max(0, ...item.lesson.sentences.map((sentence) => sentence.end))

  useEffect(() => { currentIndexRef.current = currentIndex }, [currentIndex])
  useEffect(() => { isPlayingRef.current = isPlaying }, [isPlaying])
  useEffect(() => { loopRef.current = loop }, [loop])
  useEffect(() => {
    progressRef.current = {
      last_position_seconds: Math.min(Math.max(0, currentTime), lessonDuration),
      current_sentence_id: currentSentence?.id ?? null,
      playback_rate: playbackRate,
      source_visible: sourceVisible,
      translation_visible: translationVisible,
      mode,
    }
  }, [currentSentence?.id, currentTime, lessonDuration, mode, playbackRate, sourceVisible, translationVisible])

  const persist = useCallback((force = false, changes: Partial<ListeningProgress> = {}) => {
    const snapshot = { ...progressRef.current, ...changes }
    progressRef.current = snapshot
    if (!force && Math.abs(snapshot.last_position_seconds - lastQueuedPosition.current) < PERSIST_INTERVAL_SECONDS) return
    lastQueuedPosition.current = snapshot.last_position_seconds
    writeTail.current = writeTail.current.catch(() => undefined).then(async () => {
      await updateListeningProgress(item.id, snapshot)
    }).catch(() => undefined)
  }, [item.id])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      persist(true)
    }
  }, [persist])

  useEffect(() => {
    const host = mediaHostRef.current
    if (!host) return
    let active = true
    let created: MediaController | null = null
    setMediaLoading(true)
    const applyResult = (result: Awaited<ReturnType<ListeningControllerFactory>>) => {
      if (!active) return
      created?.destroy()
      created = result.controller
      if (created) {
        created.seek(progressRef.current.last_position_seconds)
        const actualRate = created.setPlaybackRate(progressRef.current.playback_rate)
        if (PLAYBACK_RATES.includes(actualRate as ListeningPlaybackRate)) {
          setPlaybackRate(actualRate as ListeningPlaybackRate)
        }
      }
      setController(created)
      setMediaKind(result.kind)
      setMediaMessage(result.message)
      setIsPlaying(false)
    }
    void controllerFactory(item, host, applyResult).then((result) => {
      if (!active) {
        result.controller?.destroy()
        return
      }
      applyResult(result)
    }).catch(() => {
      if (active) setMediaMessage('Media player could not initialize. Transcript-only mode is active.')
    }).finally(() => {
      if (active) setMediaLoading(false)
    })
    return () => {
      active = false
      created?.destroy()
    }
  }, [controllerFactory, item])

  useEffect(() => {
    if (!controller) return
    const timer = window.setInterval(() => {
      if (!mountedRef.current) return
      let time = controller.getCurrentTime()
      const activeLoop = loopRef.current
      let range: { start: number; end: number } | null = null
      if (activeLoop.type === 'sentence') {
        const sentence = item.lesson.sentences[currentIndexRef.current]
        if (sentence && sentence.end - sentence.start >= 0.5) range = sentence
      } else if (activeLoop.type === 'custom_ab') {
        range = activeLoop
      }
      if (range && isPlayingRef.current && time >= range.end - 0.08) {
        controller.seek(range.start)
        void controller.play().catch(() => setIsPlaying(false))
        time = range.start
      }
      setCurrentTime(time)
      const next = activeSentenceIndex(item.lesson.sentences, time, currentIndexRef.current)
      if (next >= 0 && next !== currentIndexRef.current) setCurrentIndex(next)
      if (Math.abs(time - lastQueuedPosition.current) >= PERSIST_INTERVAL_SECONDS) persist()
    }, CLOCK_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [controller, item.lesson.sentences, persist])

  useEffect(() => {
    let active = true
    void listStudyCandidates(item.id, learnerId).then((values) => {
      if (active) setCandidates(values)
    }).catch(() => {
      if (active) setCandidateError('Words & Chunks are temporarily unavailable. Listening still works.')
    })
    return () => { active = false }
  }, [item.id, learnerId])

  useEffect(() => {
    if (!autoFollow || !currentSentence) return
    transcriptRows.current.get(currentSentence.id)?.scrollIntoView?.({
      block: 'nearest',
      behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    })
  }, [autoFollow, currentSentence])

  const selectSentence = useCallback((index: number) => {
    const sentence = item.lesson.sentences[index]
    if (!sentence) return
    setCurrentIndex(index)
    setCurrentTime(sentence.start)
    setDictationAnswer('')
    setDictationResult(null)
    setBlindRevealed(false)
    controller?.seek(sentence.start)
    persist(true, {
      last_position_seconds: sentence.start,
      current_sentence_id: sentence.id,
    })
  }, [controller, item.lesson.sentences, persist])

  const seekRelative = useCallback((delta: number) => {
    if (!controller) return
    const duration = controller.getDuration() ?? lessonDuration
    const next = Math.min(duration, Math.max(0, controller.getCurrentTime() + delta))
    controller.seek(next)
    setCurrentTime(next)
    persist(true, { last_position_seconds: next })
  }, [controller, lessonDuration, persist])

  const togglePlayback = useCallback(() => {
    if (!controller) return
    if (isPlaying) {
      controller.pause()
      setIsPlaying(false)
      setCurrentTime(controller.getCurrentTime())
      persist(true, { last_position_seconds: controller.getCurrentTime() })
    } else {
      void controller.play().then(() => setIsPlaying(true)).catch(() => {
        setIsPlaying(false)
        setMediaMessage('Playback could not start. Try the saved audio or transcript controls.')
      })
    }
  }, [controller, isPlaying, persist])

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target)) return
      if (event.code === 'Space') {
        event.preventDefault()
        togglePlayback()
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        seekRelative(-5)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        seekRelative(5)
      }
    }
    window.addEventListener('keydown', keyboard)
    return () => window.removeEventListener('keydown', keyboard)
  }, [seekRelative, togglePlayback])

  const sentenceCandidates = useMemo(() => candidates.filter((candidate) =>
    candidate.provenance.sentence_id === currentSentence?.id && candidate.quality !== 'rejected',
  ), [candidates, currentSentence?.id])

  function changeRate(rate: ListeningPlaybackRate) {
    if (!controller) return
    const actual = controller.setPlaybackRate(rate)
    if (PLAYBACK_RATES.includes(actual as ListeningPlaybackRate)) {
      setPlaybackRate(actual as ListeningPlaybackRate)
      if (actual !== rate) setMediaMessage(`This player uses the nearest supported speed: ${actual}x.`)
      persist(true, { playback_rate: actual as ListeningPlaybackRate })
    } else {
      setMediaMessage('This player could not apply the requested speed.')
    }
  }

  function toggleSentenceLoop() {
    if (!currentSentence || currentSentence.end - currentSentence.start < 0.5) {
      setLoopError('This sentence is too short to repeat safely.')
      return
    }
    setLoop((value) => value.type === 'sentence' ? { type: 'none' } : { type: 'sentence' })
    setCustomA(null)
    setLoopError(null)
  }

  function setB() {
    if (!controller) return
    const range = validCustomLoop(customA, controller.getCurrentTime(), controller.getDuration())
    if (!range) {
      setLoopError('B must be at least 0.5 seconds after A.')
      return
    }
    setLoop({ type: 'custom_ab', ...range })
    setLoopError(null)
  }

  function changeMode(next: ListeningMode) {
    setMode(next)
    setBlindRevealed(false)
    setDictationAnswer('')
    setDictationResult(null)
    persist(true, { mode: next })
  }

  function checkDictation(event: FormEvent) {
    event.preventDefault()
    if (currentSentence && dictationAnswer.trim()) {
      setDictationResult(compareDictation(currentSentence.text, dictationAnswer))
    }
  }

  async function saveCandidate(candidate: StudyCandidate) {
    setSavingCandidate(candidate.candidate_id)
    setCandidateError(null)
    try {
      const [saved] = await saveStudyItems([candidateInput(learnerId, candidate)])
      setCandidates((values) => values.map((value) =>
        value.candidate_id === candidate.candidate_id
          ? { ...value, already_saved: true, study_item_id: saved.id }
          : value,
      ))
    } catch {
      setCandidateError('This item could not be saved. Please try again.')
    } finally {
      setSavingCandidate(null)
    }
  }

  const hideText = mode === 'blind' && !blindRevealed
  return (
    <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={onBack} className="text-sm font-semibold text-[#176b5b]">Back to Listening</button>
        <p className="text-xs text-[#66726e]">Shortcuts: Space play/pause · ← / → five seconds</p>
      </div>
      <h1 className="mt-4 text-3xl font-semibold">{item.lesson.title}</h1>
      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <section aria-label="Listening media and controls" className="min-w-0">
          <div className="aspect-video overflow-hidden bg-[#17211f] text-white">
            <div ref={mediaHostRef} className="h-full w-full" />
            {mediaKind !== 'youtube' && <div className="-mt-[56.25%] flex aspect-video items-center justify-center px-8 text-center"><p>{mediaKind === 'audio' ? 'Saved audio player' : 'Transcript-only practice'}</p></div>}
          </div>
          {mediaLoading && <p role="status" className="mt-3">Preparing media player…</p>}
          {mediaMessage && <p role="status" className="mt-3 border-l-4 border-[#b7791f] bg-[#fff8e8] px-4 py-3 text-sm">{mediaMessage}</p>}

          <div className="mt-4 border-y border-[#ccd6d1] bg-white p-4">
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={!controller} onClick={togglePlayback} className="h-10 bg-[#176b5b] px-5 text-white disabled:bg-[#9aaba5]">{isPlaying ? 'Pause' : 'Play'}</button>
              <button type="button" disabled={!controller} onClick={() => seekRelative(-5)} className="h-10 border border-[#aebcb6] px-4">-5 seconds</button>
              <button type="button" disabled={!controller} onClick={() => seekRelative(5)} className="h-10 border border-[#aebcb6] px-4">+5 seconds</button>
              <span className="self-center text-sm tabular-nums">{formatTime(currentTime)} / {formatTime(controller?.getDuration() ?? lessonDuration)}</span>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="Playback speed">
              {PLAYBACK_RATES.map((rate) => <button key={rate} type="button" disabled={!controller} aria-pressed={playbackRate === rate} onClick={() => changeRate(rate)} className="h-9 border border-[#aebcb6] px-3 aria-pressed:bg-[#dce9e4]">{rate}x</button>)}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" aria-pressed={loop.type === 'sentence'} onClick={toggleSentenceLoop} className="h-9 border border-[#176b5b] px-3 aria-pressed:bg-[#dce9e4]">Repeat sentence</button>
              <button type="button" disabled={!controller} onClick={() => { setCustomA(controller?.getCurrentTime() ?? null); setLoop({ type: 'none' }); setLoopError(null) }} className="h-9 border border-[#aebcb6] px-3">Set A</button>
              <button type="button" disabled={!controller || customA === null} onClick={setB} className="h-9 border border-[#aebcb6] px-3">Set B</button>
              <button type="button" onClick={() => { setCustomA(null); setLoop({ type: 'none' }); setLoopError(null) }} className="h-9 border border-[#aebcb6] px-3">Clear A-B</button>
              <span className="self-center text-xs tabular-nums">A {customA === null ? '—' : formatTime(customA)} · B {loop.type === 'custom_ab' ? formatTime(loop.end) : '—'}</span>
            </div>
            {loopError && <p role="alert" className="mt-2 text-sm text-[#8f1d14]">{loopError}</p>}
          </div>

          <div className="mt-5 border-y border-[#ccd6d1] bg-white p-5">
            <p className="text-xs font-semibold tracking-wider text-[#66726e]">CURRENT SENTENCE {String(currentIndex + 1).padStart(2, '0')} / {String(item.lesson.sentences.length).padStart(2, '0')}</p>
            {currentSentence ? <>
              <p className={`mt-3 text-xl font-medium ${hideText || !sourceVisible ? 'select-none text-transparent [text-shadow:0_0_10px_#80908a]' : ''}`}>{hideText || !sourceVisible ? 'Source text hidden' : currentSentence.text}</p>
              <p className={`mt-2 ${hideText || !translationVisible ? 'select-none text-transparent [text-shadow:0_0_10px_#80908a]' : 'text-[#596560]'}`}>{hideText || !translationVisible ? 'Translation hidden' : currentSentence.translation}</p>
              {mode === 'blind' && <button type="button" onClick={() => setBlindRevealed((value) => !value)} className="mt-3 text-sm font-semibold text-[#176b5b]">{blindRevealed ? 'Hide again' : 'Reveal text'}</button>}
            </> : <p className="mt-3">This Lesson has no transcript sentences.</p>}
          </div>

          <div className="mt-5 flex flex-wrap gap-2" aria-label="Listening mode">
            {(['listen', 'blind', 'dictation'] as const).map((value) => <button key={value} type="button" aria-pressed={mode === value} onClick={() => changeMode(value)} className="h-10 border border-[#176b5b] px-4 aria-pressed:bg-[#176b5b] aria-pressed:text-white">{value[0].toUpperCase() + value.slice(1)}</button>)}
            <button type="button" aria-pressed={sourceVisible} onClick={() => { const next = !sourceVisible; setSourceVisible(next); persist(true, { source_visible: next }) }} className="h-10 border border-[#aebcb6] px-4">Show source</button>
            <button type="button" aria-pressed={translationVisible} onClick={() => { const next = !translationVisible; setTranslationVisible(next); persist(true, { translation_visible: next }) }} className="h-10 border border-[#aebcb6] px-4">Show translation</button>
          </div>

          {mode === 'dictation' && currentSentence && <form className="mt-5 border-y border-[#ccd6d1] bg-white p-5" onSubmit={checkDictation}>
            <label htmlFor="listening-dictation" className="font-semibold">Write what you hear</label>
            <textarea id="listening-dictation" rows={3} value={dictationAnswer} disabled={dictationResult !== null} onChange={(event) => { setDictationAnswer(event.target.value); setDictationResult(null) }} className="mt-3 w-full border border-[#aebcb6] p-3" />
            <div className="mt-3 flex gap-2"><button type="submit" disabled={!dictationAnswer.trim() || dictationResult !== null} className="h-9 bg-[#176b5b] px-4 text-white disabled:bg-[#9aaba5]">Check</button><button type="button" disabled={!dictationResult} onClick={() => { setDictationAnswer(''); setDictationResult(null) }} className="h-9 border border-[#aebcb6] px-4">Try Again</button></div>
            {dictationResult && <div aria-live="polite" className="mt-4"><p className="font-semibold">Accuracy {dictationResult.accuracy}%</p><div aria-label="Target word comparison" className="mt-2 flex flex-wrap gap-1">{dictationResult.targetWords.map((word, index) => <span key={`${word.value}-${index}`} className={word.status === 'correct' ? 'bg-[#e2eee9] px-1' : 'bg-[#fbe7e5] px-1 line-through'}>{word.value}</span>)}</div></div>}
          </form>}

          <section aria-labelledby="sentence-study-title" className="mt-5 border-y border-[#ccd6d1] bg-white p-5">
            <h2 id="sentence-study-title" className="text-lg font-semibold">Words & Chunks</h2>
            {candidateError && <p role="alert" className="mt-3 text-sm text-[#8f1d14]">{candidateError}</p>}
            {!candidateError && sentenceCandidates.length === 0 && <p className="mt-3 text-sm text-[#66726e]">No recommended or review candidates for this sentence.</p>}
            {(['word', 'chunk'] as const).map((kind) => {
              const values = sentenceCandidates.filter((candidate) => candidate.kind === kind)
              return values.length > 0 && <div key={kind} className="mt-4"><h3 className="text-sm font-semibold">{kind === 'word' ? 'Words' : 'Chunks'}</h3><ul className="mt-2 space-y-2">{values.map((candidate) => <li key={candidate.candidate_id} className="flex items-center justify-between gap-3 border-t border-[#dfe7e3] pt-2"><span><strong>{candidate.text}</strong> · {candidate.meaning || 'Meaning unavailable'} <small>({candidate.quality})</small></span>{candidate.already_saved ? <span className="text-sm text-[#176b5b]">Saved</span> : <button type="button" disabled={savingCandidate === candidate.candidate_id} onClick={() => void saveCandidate(candidate)} className="h-8 border border-[#176b5b] px-3">Save</button>}</li>)}</ul></div>
            })}
          </section>
        </section>

        <section aria-labelledby="synchronized-transcript-title" className="min-w-0">
          <div className="flex items-center justify-between gap-3">
            <h2 id="synchronized-transcript-title" className="text-xl font-semibold">Synchronized transcript</h2>
            <button type="button" aria-pressed={autoFollow} onClick={() => setAutoFollow((value) => !value)} className="h-9 border border-[#aebcb6] px-3">Auto-follow {autoFollow ? 'ON' : 'OFF'}</button>
          </div>
          <ol className="mt-4 max-h-[75vh] space-y-2 overflow-y-auto pr-2">
            {item.lesson.sentences.map((sentence, index) => {
              const active = index === currentIndex
              return <li key={sentence.id}><button ref={(element) => { if (element) transcriptRows.current.set(sentence.id, element); else transcriptRows.current.delete(sentence.id) }} type="button" aria-current={active ? 'true' : undefined} onClick={() => selectSentence(index)} className="w-full border-l-4 border-transparent bg-white p-4 text-left aria-[current=true]:border-[#176b5b] aria-[current=true]:bg-[#e8f1ed]">
                <span className="block text-xs tabular-nums text-[#66726e]">{formatTime(sentence.start)}</span>
                <span className={`mt-1 block font-medium ${hideText || !sourceVisible ? 'select-none text-transparent [text-shadow:0_0_8px_#80908a]' : ''}`}>{hideText || !sourceVisible ? 'Source text hidden' : sentence.text}</span>
                <span className={`mt-1 block text-sm ${hideText || !translationVisible ? 'select-none text-transparent [text-shadow:0_0_8px_#80908a]' : 'text-[#596560]'}`}>{hideText || !translationVisible ? 'Translation hidden' : sentence.translation}</span>
              </button></li>
            })}
          </ol>
        </section>
      </div>
    </main>
  )
}
