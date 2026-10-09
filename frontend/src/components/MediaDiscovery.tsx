import { useEffect, useRef, useState, type FormEvent } from 'react'
import { discoverMedia, safeMediaUrl } from '../api/mediaDiscovery'
import type { SourceLanguage } from '../types/lesson'
import type { MediaCandidate, TrainingGoal } from '../types/mediaDiscovery'

interface Props {
  sourceLanguage: SourceLanguage
  isGenerating: boolean
  onSelect: (url: string) => Promise<void>
}

const inputStyle = 'h-11 w-full border border-[#aebcb6] bg-white px-3 focus:ring-2 focus:ring-[#176b5b]/20'
const buttonStyle = 'min-h-11 bg-[#176b5b] px-5 py-2 text-sm font-semibold text-white focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2 disabled:opacity-50'
const practiceGoals: ReadonlyArray<{ value: TrainingGoal; label: string }> = [
  { value: 'general', label: 'General' },
  { value: 'blind_listening', label: 'Blind Listening' },
  { value: 'comprehension', label: 'Comprehension' },
  { value: 'mini_dictation', label: 'Mini Dictation' },
  { value: 'shadowing', label: 'Shadowing' },
  { value: 'retell', label: 'Retell' },
]

export default function MediaDiscovery({ sourceLanguage, isGenerating, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [minimum, setMinimum] = useState('60')
  const [maximum, setMaximum] = useState('1800')
  const [limit, setLimit] = useState('5')
  const [practiceGoal, setPracticeGoal] = useState<TrainingGoal>('general')
  const [results, setResults] = useState<MediaCandidate[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  function invalidate() {
    controller.current?.abort()
    controller.current = null
    setLoading(false)
    setResults(null)
    setError(null)
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (controller.current || isGenerating) return
    const min = Number(minimum), max = Number(maximum), count = Number(limit)
    if (!query.trim() || query.trim().length > 200 || !minimum || !maximum || !limit ||
      !Number.isInteger(min) || !Number.isInteger(max) || !Number.isInteger(count) ||
      min < 0 || max < 1 || min > max || max > 14400 || count < 1 || count > 10) {
      setError('Enter a topic, a valid duration range (0–14400 seconds), and 1–10 results.')
      return
    }
    const request = new AbortController()
    controller.current = request
    setLoading(true)
    setError(null)
    setResults(null)
    try {
      const candidates = await discoverMedia({ query: query.trim(), target_language: sourceLanguage,
        training_goal: practiceGoal, min_duration_seconds: min, max_duration_seconds: max,
        limit: count }, request.signal)
      if (controller.current === request) setResults(candidates)
    } catch (caught) {
      if (controller.current === request) setError(caught instanceof Error ? caught.message : 'Video search failed. Please try again.')
    } finally {
      if (controller.current === request) {
        controller.current = null
        setLoading(false)
      }
    }
  }

  return <div className="mt-5">
    <form onSubmit={search} className="space-y-4">
      <label className="block">Search topic
        <input className={inputStyle} value={query} required maxLength={200}
          onChange={event => { invalidate(); setQuery(event.target.value) }} />
      </label>
      <div className="max-w-sm">
        <label className="block" htmlFor="practice-goal">Practice Goal</label>
        <select id="practice-goal" className={inputStyle} value={practiceGoal}
          aria-describedby="practice-goal-help"
          onChange={event => { invalidate(); setPracticeGoal(event.target.value as TrainingGoal) }}>
          {practiceGoals.map(goal => <option key={goal.value} value={goal.value}>{goal.label}</option>)}
        </select>
        <p id="practice-goal-help" className="mt-1 text-sm text-[#596560]">
          Choose a goal to rank videos for the practice you want to do.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <label>Minimum duration (seconds)<input className={inputStyle} type="number" required min={0} max={14400} step={1} value={minimum} onChange={event => { invalidate(); setMinimum(event.target.value) }} /></label>
        <label>Maximum duration (seconds)<input className={inputStyle} type="number" required min={1} max={14400} step={1} value={maximum} onChange={event => { invalidate(); setMaximum(event.target.value) }} /></label>
        <label>Number of results<input className={inputStyle} type="number" required min={1} max={10} step={1} value={limit} onChange={event => { invalidate(); setLimit(event.target.value) }} /></label>
      </div>
      <button className={buttonStyle} disabled={loading || isGenerating} type="submit">{loading ? 'Searching...' : 'Find videos'}</button>
    </form>
    <div className="mt-4" role="status" aria-live="polite">
      {loading && <p>Searching for videos...</p>}
      {results?.length === 0 && <p>No suitable videos found. Try another topic or duration range.</p>}
    </div>
    {error && <p role="alert" className="mt-4 text-[#8f1d14]">{error}</p>}
    <div className="mt-4 grid gap-4 sm:grid-cols-2">
      {results?.map(candidate => {
        const thumbnail = safeMediaUrl(candidate.thumbnail)
        const url = safeMediaUrl(candidate.url, true)
        const seconds = Math.round(candidate.duration_seconds)
        return <article key={candidate.provider_id} className="min-w-0 border border-[#ccd6d1] bg-white p-4 [overflow-wrap:anywhere]">
          {thumbnail && <img src={thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" className="mb-3 aspect-video w-full object-cover" />}
          <h3 className="font-semibold">{candidate.title}</h3>
          <p>{candidate.channel || 'Unknown channel'} · {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</p>
          <ul className="my-3 text-sm">
            {candidate.subtitle_tracks.map(track => <li key={track.language}>{track.language}: {[
              track.has_human && 'Human subtitles', track.has_automatic && 'Automatic captions',
            ].filter(Boolean).join(' · ') || 'No captions'}</li>)}
            {candidate.subtitle_tracks.length === 0 && <li>No captions available</li>}
          </ul>
          <p className="font-medium">Suitability score: {candidate.suitability.total}/100</p>
          <ul className="my-3 list-disc pl-5 text-sm">{candidate.suitability.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
          <button type="button" className={buttonStyle} disabled={!url || isGenerating || loading} aria-label={`Use this video: ${candidate.title}`} onClick={() => { if (url) void onSelect(url) }}>Use this video</button>
          {!url && <p className="mt-2 text-sm">This video URL is unavailable.</p>}
        </article>
      })}
    </div>
  </div>
}
