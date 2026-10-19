import { useEffect, useRef, useState } from 'react'

import { ShadowingApiError, submitShadowingAttempt } from '../api/library'
import { API_BASE_URL } from '../api/apiBase'
import { createAudioMediaController, type MediaController } from '../listening/mediaController'
import { useSyncRecorder } from '../shadowing/useSyncRecorder'
import type { LibraryItem, ShadowingAttemptResponse, ShadowingMetrics, ShadowingProgress } from '../types/library'


interface Props {
  item: LibraryItem
  initialSentenceId?: number | null
  onBack: () => void
  controllerFactory?: (url: string, onError: () => void) => MediaController
}

function signed(value: number, unit: string): string { return `${value >= 0 ? '+' : ''}${value}${unit}` }
function metric(value: number | null, unit = ' ms'): string { return value === null ? 'Unavailable' : `${value}${unit}` }

export default function ShadowingWorkspace({
  item,
  initialSentenceId = null,
  onBack,
  controllerFactory = createAudioMediaController,
}: Props) {
  const restoredId = initialSentenceId ?? item.shadowing_progress.last_sentence_id
  const restoredIndex = Math.max(0, item.lesson.sentences.findIndex((sentence) => sentence.id === restoredId))
  const [index, setIndex] = useState(restoredIndex)
  const [progress, setProgress] = useState<ShadowingProgress>(item.shadowing_progress)
  const [referenceRate, setReferenceRate] = useState(1)
  const [result, setResult] = useState<ShadowingAttemptResponse | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<MediaController | null>(null)
  const referenceTimer = useRef<number | null>(null)
  const uploadedBlob = useRef<Blob | null>(null)
  const sentence = item.lesson.sentences[index] ?? null
  const duration = sentence ? Math.max(0, sentence.end - sentence.start) : 0
  const recorder = useSyncRecorder(duration)
  const active = ['requesting', 'countdown', 'recording'].includes(recorder.status)
  const sentenceProgress = sentence ? progress.sentences[String(sentence.id)] : undefined

  useEffect(() => {
    if (item.persistent_audio.status !== 'available' || !item.lesson.audio_url) return
    const controller = controllerFactory(`${API_BASE_URL}${item.lesson.audio_url}`, () => setError('Saved reference audio could not be played.'))
    controllerRef.current = controller
    return () => { controller.destroy(); controllerRef.current = null }
  }, [controllerFactory, item.lesson.audio_url, item.persistent_audio.status])

  useEffect(() => () => {
    if (referenceTimer.current !== null) window.clearTimeout(referenceTimer.current)
  }, [])

  useEffect(() => {
    const blob = recorder.blob
    if (!blob || !sentence || uploadedBlob.current === blob) return
    uploadedBlob.current = blob
    let current = true
    setAnalyzing(true)
    setError(null)
    void submitShadowingAttempt(item.id, sentence.id, sentenceProgress?.attempt_count ?? 0, blob, recorder.recordedDuration)
      .then((value) => {
        if (!current) return
        setResult(value)
        setProgress((previous) => ({
          last_sentence_id: sentence.id,
          last_practiced_at: value.progress.last_attempt_at,
          sentences: { ...previous.sentences, [String(sentence.id)]: value.progress },
        }))
      })
      .catch((caught) => {
        if (!current) return
        setError(caught instanceof ShadowingApiError && caught.status === 409
          ? 'This sentence was updated elsewhere. Reopen Shadowing to refresh its attempt count.'
          : 'Local shadowing analysis failed. Your recording remains available to play or retry.')
      })
      .finally(() => { if (current) setAnalyzing(false) })
    return () => { current = false }
  }, [item.id, recorder.blob, recorder.recordedDuration, sentence, sentenceProgress?.attempt_count])

  function stopReferenceTimer() {
    if (referenceTimer.current !== null) window.clearTimeout(referenceTimer.current)
    referenceTimer.current = null
  }

  function playReference() {
    if (!sentence || !controllerRef.current) return
    stopReferenceTimer()
    const controller = controllerRef.current
    controller.pause()
    controller.seek(sentence.start)
    const actual = controller.setPlaybackRate(referenceRate)
    void controller.play().catch(() => setError('Reference playback could not start.'))
    referenceTimer.current = window.setTimeout(() => controller.pause(), duration / Math.max(actual, .5) * 1_000)
  }

  function select(next: number) {
    if (active || next < 0 || next >= item.lesson.sentences.length) return
    stopReferenceTimer()
    controllerRef.current?.pause()
    recorder.reset()
    uploadedBlob.current = null
    setResult(null)
    setError(null)
    setIndex(next)
  }

  function tryAgain() {
    recorder.reset()
    uploadedBlob.current = null
    setResult(null)
    setError(null)
  }

  const shownMetrics: ShadowingMetrics | null = result?.metrics ?? sentenceProgress?.last_metrics ?? null
  if (!sentence) return <main className="mx-auto max-w-5xl px-5 py-10"><button type="button" onClick={onBack}>Back to Shadowing</button><h1 className="mt-5 text-3xl font-semibold">{item.lesson.title}</h1><p className="mt-6">This Lesson has no sentences to shadow.</p></main>

  return <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8">
    <button type="button" onClick={onBack} disabled={active} className="text-sm font-semibold text-[#176b5b]">Back to Shadowing</button>
    <h1 className="mt-4 text-3xl font-semibold">Sync Shadowing</h1>
    <p className="mt-1 text-[#66726e]">{item.lesson.title} · Sentence {index + 1} of {item.lesson.sentences.length}</p>
    <p className="mt-4 border-l-4 border-[#b7791f] bg-[#fff8e8] p-3 text-sm"><strong>Headphones recommended for accurate sync analysis.</strong> Echo cancellation helps but cannot fully remove speaker playback.</p>
    {item.persistent_audio.status !== 'available' && <p role="status" className="mt-3 border-l-4 border-[#b7791f] bg-white p-3">Precise sync timing requires saved reference audio. Transcript Match remains available.</p>}
    {error && <p role="alert" className="mt-3 text-[#8f1d14]">{error}</p>}

    <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_1fr]">
      <section className="border-y border-[#ccd6d1] bg-white p-5" aria-label="Reference and recording">
        <h2 className="text-lg font-semibold">Reference</h2>
        <p className="mt-4 text-xl font-medium">{sentence.text}</p>
        <p className="mt-2 text-[#66726e]">{sentence.translation}</p>
        <div className="mt-5 flex flex-wrap gap-2">
          <button type="button" disabled={item.persistent_audio.status !== 'available' || !item.lesson.audio_url || active} onClick={playReference} className="h-10 bg-[#176b5b] px-4 text-white disabled:bg-[#9aaba5]">Play sentence</button>
          {[0.75, 1, 1.25].map((rate) => <button key={rate} type="button" disabled={active} aria-pressed={referenceRate === rate} onClick={() => setReferenceRate(rate)} className="h-10 border border-[#aebcb6] px-3 aria-pressed:bg-[#dce9e4]">{rate}x</button>)}
        </div>
        <p className="mt-3 text-xs text-[#66726e]">Preview speed may vary. Scored sync attempts always use the original 1.0x reference.</p>

        <div className="mt-7 border-t border-[#ccd6d1] pt-5">
          <button type="button" disabled={active || analyzing} onClick={() => void recorder.start(controllerRef.current, sentence.start)} className="h-12 bg-[#176b5b] px-6 font-semibold text-white disabled:bg-[#9aaba5]">Start Sync Attempt</button>
          <div aria-live="polite" className="mt-4 min-h-8">
            {recorder.status === 'requesting' && <p>Waiting for microphone permission…</p>}
            {recorder.status === 'countdown' && <p className="text-3xl font-semibold">{recorder.countdown}</p>}
            {recorder.status === 'recording' && <p className="font-semibold text-[#8f1d14]">Recording + Original</p>}
            {analyzing && <p>Analyzing locally…</p>}
            {recorder.error && <p role="alert" className="text-[#8f1d14]">{recorder.error}</p>}
          </div>
          {recorder.playbackUrl && <div className="mt-4"><p className="text-sm font-semibold">Play My Recording</p><audio className="mt-2 w-full" controls src={recorder.playbackUrl} /></div>}
        </div>
      </section>

      <section className="border-y border-[#ccd6d1] bg-white p-5" aria-label="Shadowing result">
        <h2 className="text-lg font-semibold">{result ? 'Attempt result' : sentenceProgress?.last_metrics ? 'Last attempt' : 'Practice result'}</h2>
        {!shownMetrics && <p className="mt-4 text-[#66726e]">Complete a sync attempt to see local transcript and timing evidence.</p>}
        {shownMetrics && <>
          <div className="mt-5"><p className="text-sm text-[#66726e]">Transcript Match <span title="Word-sequence match from local speech recognition.">(local ASR)</span></p><p className="text-3xl font-semibold">{shownMetrics.transcript_match_percent.toFixed(1)}%</p></div>
          <p className="mt-4"><strong>Recognized:</strong> {shownMetrics.recognized_text || 'No speech detected'}</p>
          <div className="mt-4 grid grid-cols-2 gap-2 text-sm"><p>Matched {shownMetrics.matched_count}</p><p>Missed {shownMetrics.missed_count}</p><p>Extra {shownMetrics.extra_count}</p><p>Substituted {shownMetrics.substituted_count}</p></div>
          <div className="mt-4 space-y-1 border-t border-[#dfe7e3] pt-4 text-sm">
            <p title="How early or late your first aligned word began.">Start delay: {shownMetrics.start_delay_ms === null ? 'Unavailable' : signed(shownMetrics.start_delay_ms, ' ms')}</p>
            <p>Reference speech: {shownMetrics.reference_speech_duration_ms === null ? 'Unavailable' : `${(shownMetrics.reference_speech_duration_ms / 1000).toFixed(2)}s`}</p>
            <p>Your speech: {shownMetrics.attempt_speech_duration_ms === null ? 'Unavailable' : `${(shownMetrics.attempt_speech_duration_ms / 1000).toFixed(2)}s`}</p>
            <p>Speech difference: {shownMetrics.duration_delta_ms === null ? 'Unavailable' : signed(shownMetrics.duration_delta_ms / 1000, ' s')}</p>
            <p title="Average deviation of matched word starts after removing initial start delay.">Word timing MAE: {metric(shownMetrics.word_timing_mae_ms)}</p>
            <p title="Average difference between aligned inter-word pauses.">Pause MAE: {metric(shownMetrics.pause_mae_ms)}</p>
          </div>
          {!shownMetrics.timing_reference_available && <p className="mt-3 text-sm text-[#66726e]">Precise timing unavailable: {shownMetrics.timing_unavailable_reason?.replaceAll('_', ' ') ?? 'Unavailable'}.</p>}
          <p className="mt-5 text-lg font-semibold">{shownMetrics.sync_target_met ? 'Sync target met' : 'Keep practicing'}</p>
          {(result?.stable_sync_achieved || (sentenceProgress?.consecutive_target_hits ?? 0) >= 3) && <p className="mt-2 font-semibold text-[#176b5b]">Stable sync achieved</p>}
          <div className="mt-4 space-y-1 text-sm">
            {(['missed', 'extra', 'substituted'] as const).map((status) => {
              const values = shownMetrics.aligned_words.filter((word) => word.status === status)
              return values.length > 0 && <p key={status}><strong>{status[0].toUpperCase() + status.slice(1)}:</strong> {values.map((word) => status === 'substituted' ? `${word.target} → ${word.recognized}` : word.target ?? word.recognized).join(', ')}</p>
            })}
          </div>
        </>}
        {(result || recorder.status === 'recorded') && !analyzing && <button type="button" onClick={tryAgain} className="mt-6 h-10 border border-[#176b5b] px-4">Try Again</button>}
      </section>
    </div>

    <div className="mt-6 flex flex-wrap justify-between gap-3">
      <button type="button" disabled={active || index === 0} onClick={() => select(index - 1)} className="h-10 border border-[#aebcb6] px-4 disabled:opacity-50">Previous sentence</button>
      <button type="button" disabled={active || index === item.lesson.sentences.length - 1} onClick={() => select(index + 1)} className="h-10 border border-[#aebcb6] px-4 disabled:opacity-50">Next sentence</button>
    </div>
    <ol className="mt-6 grid gap-2 sm:grid-cols-2">{item.lesson.sentences.map((value, sentenceIndex) => <li key={value.id}><button type="button" disabled={active} aria-current={sentenceIndex === index ? 'true' : undefined} onClick={() => select(sentenceIndex)} className="w-full border-l-4 border-transparent bg-white p-3 text-left aria-[current=true]:border-[#176b5b] aria-[current=true]:bg-[#e8f1ed]">{sentenceIndex + 1}. {value.text}</button></li>)}</ol>
  </main>
}
