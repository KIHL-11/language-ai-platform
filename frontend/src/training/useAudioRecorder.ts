import { useCallback, useEffect, useRef, useState } from 'react'

export type AudioRecorderStatus =
  | 'idle'
  | 'requesting_permission'
  | 'recording'
  | 'recorded'
  | 'error'

interface AudioRecorderState {
  status: AudioRecorderStatus
  recordedBlob: Blob | null
  recordedDuration: number
  playbackUrl: string | null
  elapsedSeconds: number
  error: string | null
  automaticallyStopped: boolean
}

const INITIAL_STATE: AudioRecorderState = {
  status: 'idle',
  recordedBlob: null,
  recordedDuration: 0,
  playbackUrl: null,
  elapsedSeconds: 0,
  error: null,
  automaticallyStopped: false,
}

function recorderError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return 'Microphone permission was denied. Allow access and try again.'
  }
  if (error instanceof DOMException && error.name === 'NotFoundError') {
    return 'No microphone was found. Connect one and try again.'
  }
  return 'Microphone access failed. Check your device and browser settings.'
}

function stopTracks(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop())
}

export function useAudioRecorder(maxRecordingSeconds: number) {
  const [state, setState] = useState<AudioRecorderState>(INITIAL_STATE)
  const mountedRef = useRef(true)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const startedAtRef = useRef(0)
  const maximumTimerRef = useRef<number | null>(null)
  const automaticallyStoppedRef = useRef(false)
  const playbackUrlRef = useRef<string | null>(null)
  const requestIdRef = useRef(0)

  const clearMaximumTimer = useCallback(() => {
    if (maximumTimerRef.current !== null) {
      window.clearTimeout(maximumTimerRef.current)
      maximumTimerRef.current = null
    }
  }, [])

  const releasePlaybackUrl = useCallback(() => {
    if (playbackUrlRef.current) {
      URL.revokeObjectURL(playbackUrlRef.current)
      playbackUrlRef.current = null
    }
  }, [])

  const reset = useCallback(() => {
    requestIdRef.current += 1
    clearMaximumTimer()
    automaticallyStoppedRef.current = false
    const recorder = recorderRef.current
    recorderRef.current = null
    if (recorder && recorder.state !== 'inactive') {
      recorder.ondataavailable = null
      recorder.onstop = null
      recorder.onerror = null
      recorder.stop()
    }
    stopTracks(streamRef.current)
    streamRef.current = null
    chunksRef.current = []
    releasePlaybackUrl()
    setState(INITIAL_STATE)
  }, [clearMaximumTimer, releasePlaybackUrl])

  const stopActiveRecording = useCallback((automaticallyStopped: boolean) => {
    clearMaximumTimer()
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') {
      return
    }
    automaticallyStoppedRef.current = automaticallyStopped
    recorder.stop()
    stopTracks(streamRef.current)
  }, [clearMaximumTimer])

  const startRecording = useCallback(async () => {
    if (recorderRef.current || state.status === 'requesting_permission') {
      return
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setState({
        ...INITIAL_STATE,
        status: 'error',
        error: 'A microphone is not available in this browser.',
      })
      return
    }
    if (typeof MediaRecorder === 'undefined') {
      setState({
        ...INITIAL_STATE,
        status: 'error',
        error: 'Audio recording is not supported in this browser.',
      })
      return
    }

    releasePlaybackUrl()
    clearMaximumTimer()
    automaticallyStoppedRef.current = false
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setState({ ...INITIAL_STATE, status: 'requesting_permission' })

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mountedRef.current || requestIdRef.current !== requestId) {
        stopTracks(stream)
        return
      }

      streamRef.current = stream
      const recorder = new MediaRecorder(stream)
      recorderRef.current = recorder
      chunksRef.current = []

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data)
        }
      }
      recorder.onerror = () => {
        clearMaximumTimer()
        requestIdRef.current += 1
        stopTracks(streamRef.current)
        streamRef.current = null
        recorderRef.current = null
        if (mountedRef.current) {
          setState({
            ...INITIAL_STATE,
            status: 'error',
            error: 'Recording failed. Check the microphone and try again.',
          })
        }
      }
      recorder.onstop = () => {
        clearMaximumTimer()
        // performance.now() is monotonic, so wall-clock changes cannot skew a recording.
        const recordedDuration = Math.max(
          0,
          (performance.now() - startedAtRef.current) / 1000,
        )
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        })
        recorderRef.current = null
        chunksRef.current = []
        stopTracks(streamRef.current)
        streamRef.current = null

        if (!mountedRef.current || requestIdRef.current !== requestId) {
          return
        }
        releasePlaybackUrl()
        const playbackUrl = URL.createObjectURL(blob)
        playbackUrlRef.current = playbackUrl
        setState({
          status: 'recorded',
          recordedBlob: blob,
          recordedDuration,
          playbackUrl,
          elapsedSeconds: recordedDuration,
          error: null,
          automaticallyStopped: automaticallyStoppedRef.current,
        })
      }

      startedAtRef.current = performance.now()
      recorder.start()
      maximumTimerRef.current = window.setTimeout(() => {
        stopActiveRecording(true)
      }, maxRecordingSeconds * 1000)
      setState({ ...INITIAL_STATE, status: 'recording' })
    } catch (error) {
      clearMaximumTimer()
      stopTracks(streamRef.current)
      streamRef.current = null
      recorderRef.current = null
      if (mountedRef.current && requestIdRef.current === requestId) {
        setState({
          ...INITIAL_STATE,
          status: 'error',
          error: recorderError(error),
        })
      }
    }
  }, [
    clearMaximumTimer,
    maxRecordingSeconds,
    releasePlaybackUrl,
    state.status,
    stopActiveRecording,
  ])

  const stopRecording = useCallback(() => {
    stopActiveRecording(false)
  }, [stopActiveRecording])

  useEffect(() => {
    if (state.status !== 'recording') {
      return
    }
    const timer = window.setInterval(() => {
      setState((current) => ({
        ...current,
        elapsedSeconds: Math.max(
          0,
          (performance.now() - startedAtRef.current) / 1000,
        ),
      }))
    }, 250)
    return () => window.clearInterval(timer)
  }, [state.status])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestIdRef.current += 1
      clearMaximumTimer()
      const recorder = recorderRef.current
      if (recorder && recorder.state !== 'inactive') {
        recorder.ondataavailable = null
        recorder.onstop = null
        recorder.onerror = null
        recorder.stop()
      }
      stopTracks(streamRef.current)
      releasePlaybackUrl()
    }
  }, [clearMaximumTimer, releasePlaybackUrl])

  return {
    ...state,
    startRecording,
    stopRecording,
    reset,
  }
}
