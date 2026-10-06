import { useEffect, useRef, useState } from 'react'

interface SegmentPlayerProps {
  audioUrl: string | null
  start: number
  end: number
}

const PLAYBACK_RATES = [0.75, 1, 1.25] as const

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.max(0, seconds - minutes * 60)
  return `${minutes}:${remainingSeconds.toFixed(1).padStart(4, '0')}`
}

export default function SegmentPlayer({
  audioUrl,
  start,
  end,
}: SegmentPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [playbackRate, setPlaybackRate] = useState<(typeof PLAYBACK_RATES)[number]>(1)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) {
      return
    }
    audio.pause()
    setIsPlaying(false)
    setError(null)
    if (audio.readyState > 0) {
      audio.currentTime = start
    }
  }, [audioUrl, start, end])

  function seekToStart() {
    const audio = audioRef.current
    if (audio) {
      audio.currentTime = start
    }
  }

  function playSegment() {
    const audio = audioRef.current
    if (!audio) {
      return
    }
    if (audio.currentTime < start || audio.currentTime >= end) {
      seekToStart()
    }
    setError(null)
    void audio.play().catch(() => {
      setIsPlaying(false)
      setError('Audio playback could not start.')
    })
  }

  function replaySegment() {
    seekToStart()
    playSegment()
  }

  function handleTimeUpdate() {
    const audio = audioRef.current
    if (audio && audio.currentTime >= end) {
      audio.pause()
      audio.currentTime = end
      setIsPlaying(false)
    }
  }

  function changePlaybackRate(rate: (typeof PLAYBACK_RATES)[number]) {
    setPlaybackRate(rate)
    if (audioRef.current) {
      audioRef.current.playbackRate = rate
    }
  }

  if (!audioUrl) {
    return (
      <p className="border-l-4 border-[#b7791f] bg-[#fff8e8] px-4 py-3 text-sm text-[#754b08]">
        Audio is not available for this lesson.
      </p>
    )
  }

  return (
    <div className="border-y border-[#c9d5d0] bg-white px-4 py-4 sm:px-5">
      <audio
        ref={audioRef}
        src={audioUrl}
        preload="metadata"
        onLoadedMetadata={seekToStart}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onEnded={() => setIsPlaying(false)}
        onTimeUpdate={handleTimeUpdate}
      />

      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              if (isPlaying) {
                audioRef.current?.pause()
              } else {
                playSegment()
              }
            }}
            className="h-10 min-w-28 bg-[#176b5b] px-4 text-sm font-semibold text-white hover:bg-[#115548] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
          >
            {isPlaying ? 'Pause' : 'Play segment'}
          </button>
          <button
            type="button"
            onClick={replaySegment}
            className="h-10 border border-[#aebcb6] bg-white px-4 text-sm font-semibold text-[#293430] hover:bg-[#f3f7f5] focus:outline-none focus:ring-2 focus:ring-[#176b5b] focus:ring-offset-2"
          >
            Replay
          </button>
          <span className="ml-1 text-xs tabular-nums text-[#66726e]">
            {formatTime(start)} - {formatTime(end)}
          </span>
        </div>

        <div className="flex items-center gap-1" aria-label="Playback speed">
          {PLAYBACK_RATES.map((rate) => (
            <button
              key={rate}
              type="button"
              aria-pressed={playbackRate === rate}
              onClick={() => changePlaybackRate(rate)}
              className={`h-8 min-w-12 border px-2 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-[#176b5b] ${
                playbackRate === rate
                  ? 'border-[#176b5b] bg-[#e2eee9] text-[#135c4e]'
                  : 'border-[#c9d5d0] bg-white text-[#596560]'
              }`}
            >
              {rate}x
            </button>
          ))}
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-[#9d241b]">{error}</p>}
    </div>
  )
}
