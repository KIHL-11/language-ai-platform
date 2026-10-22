import { useCallback, useEffect, useRef, useState } from 'react'

import type { MediaController } from '../listening/mediaController'


export type SyncRecorderStatus = 'idle' | 'requesting' | 'countdown' | 'recording' | 'recorded' | 'error'

interface SyncRecorderState {
  status: SyncRecorderStatus
  countdown: '3' | '2' | '1' | 'Go' | null
  blob: Blob | null
  playbackUrl: string | null
  recordedDuration: number
  error: string | null
}

const INITIAL: SyncRecorderState = {
  status: 'idle', countdown: null, blob: null, playbackUrl: null,
  recordedDuration: 0, error: null,
}

function microphoneError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'NotAllowedError') return 'Microphone permission was denied.'
  if (error instanceof DOMException && error.name === 'NotFoundError') return 'No microphone was found.'
  return 'Microphone access failed. Check your browser and device settings.'
}

export function maximumRecordingSeconds(targetDuration: number): number {
  return Math.min(30, Math.max(10, targetDuration * 2))
}

const RECORDING_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  'audio/ogg',
]

export function supportedRecordingMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return null
  }
  return RECORDING_MIME_TYPES.find((value) => MediaRecorder.isTypeSupported(value)) ?? null
}

export function useSyncRecorder(targetDuration: number) {
  const [state, setState] = useState<SyncRecorderState>(INITIAL)
  const mounted = useRef(true)
  const requestId = useRef(0)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const timer = useRef<number | null>(null)
  const timerResolve = useRef<(() => void) | null>(null)
  const playbackUrl = useRef<string | null>(null)
  const controller = useRef<MediaController | null>(null)
  const startedAt = useRef(0)

  const stopTracks = useCallback(() => {
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
  }, [])

  const clearTimer = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
    const resolve = timerResolve.current
    timerResolve.current = null
    resolve?.()
  }, [])

  const releaseUrl = useCallback(() => {
    if (playbackUrl.current) URL.revokeObjectURL(playbackUrl.current)
    playbackUrl.current = null
  }, [])

  const reset = useCallback(() => {
    requestId.current += 1
    clearTimer()
    controller.current?.pause()
    controller.current = null
    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.ondataavailable = null
      recorder.current.onstop = null
      recorder.current.onerror = null
      recorder.current.stop()
    }
    recorder.current = null
    stopTracks()
    chunks.current = []
    releaseUrl()
    if (mounted.current) setState(INITIAL)
  }, [clearTimer, releaseUrl, stopTracks])

  const delay = (milliseconds: number) => new Promise<void>((resolve) => {
    timerResolve.current = resolve
    timer.current = window.setTimeout(() => {
      timer.current = null
      timerResolve.current = null
      resolve()
    }, milliseconds)
  })

  const start = useCallback(async (reference: MediaController | null, startAt: number) => {
    if (state.status !== 'idle' && state.status !== 'error' && state.status !== 'recorded') return
    if (!navigator.mediaDevices?.getUserMedia) {
      setState({ ...INITIAL, status: 'error', error: 'A microphone is not available in this browser.' })
      return
    }
    if (typeof MediaRecorder === 'undefined') {
      setState({ ...INITIAL, status: 'error', error: 'Audio recording is not supported in this browser.' })
      return
    }
    reset()
    const id = requestId.current + 1
    requestId.current = id
    setState({ ...INITIAL, status: 'requesting' })
    let microphoneReady = false
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      if (!mounted.current || requestId.current !== id) {
        mediaStream.getTracks().forEach((track) => track.stop())
        return
      }
      stream.current = mediaStream
      microphoneReady = true
      for (const value of ['3', '2', '1'] as const) {
        setState({ ...INITIAL, status: 'countdown', countdown: value })
        await delay(1_000)
        if (!mounted.current || requestId.current !== id) return
      }
      setState({ ...INITIAL, status: 'countdown', countdown: 'Go' })
      await delay(250)
      if (!mounted.current || requestId.current !== id) return
      const preferredMimeType = supportedRecordingMimeType()
      const mediaRecorder = preferredMimeType
        ? new MediaRecorder(mediaStream, { mimeType: preferredMimeType })
        : new MediaRecorder(mediaStream)
      recorder.current = mediaRecorder
      chunks.current = []
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data)
      }
      mediaRecorder.onerror = () => {
        clearTimer()
        stopTracks()
        recorder.current = null
        if (mounted.current) setState({ ...INITIAL, status: 'error', error: 'Recording failed. Try again.' })
      }
      mediaRecorder.onstop = () => {
        clearTimer()
        controller.current?.pause()
        stopTracks()
        recorder.current = null
        if (!mounted.current || requestId.current !== id) return
        const type = mediaRecorder.mimeType || chunks.current.find((chunk) => chunk.type)?.type || preferredMimeType || 'audio/webm'
        const blob = new Blob(chunks.current, { type })
        chunks.current = []
        releaseUrl()
        if (blob.size === 0) {
          setState({ ...INITIAL, status: 'error', error: 'No audio was captured. Check your microphone and try again.' })
          return
        }
        const url = URL.createObjectURL(blob)
        playbackUrl.current = url
        setState({
          status: 'recorded', countdown: null, blob, playbackUrl: url,
          recordedDuration: Math.max(0, (performance.now() - startedAt.current) / 1_000),
          error: null,
        })
      }
      startedAt.current = performance.now()
      mediaRecorder.start()
      setState({ ...INITIAL, status: 'recording' })
      controller.current = reference
      if (reference) {
        reference.pause()
        reference.setPlaybackRate(1)
        reference.seek(startAt)
        await reference.play()
      }
      const captureSeconds = Math.min(maximumRecordingSeconds(targetDuration), targetDuration + 0.75)
      timer.current = window.setTimeout(() => {
        if (mediaRecorder.state !== 'inactive') mediaRecorder.stop()
      }, captureSeconds * 1_000)
    } catch (error) {
      clearTimer()
      if (recorder.current && recorder.current.state !== 'inactive') {
        recorder.current.ondataavailable = null
        recorder.current.onstop = null
        recorder.current.onerror = null
        recorder.current.stop()
      }
      stopTracks()
      recorder.current = null
      if (mounted.current && requestId.current === id) {
        setState({
          ...INITIAL,
          status: 'error',
          error: microphoneReady
            ? 'Recording or reference playback could not start. Try again.'
            : microphoneError(error),
        })
      }
    }
  }, [clearTimer, releaseUrl, reset, state.status, stopTracks, targetDuration])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      reset()
    }
  }, [reset])

  return { ...state, start, reset }
}
