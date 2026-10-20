import { GeminiLiveProvider } from './geminiLiveProvider'
import { OpenAISpeakingProvider } from './openAISpeakingProvider'
import type { SpeakingAgentProvider } from './provider'
import type { SpeakingProviderId } from '../types/speaking'

export type SpeakingProviderFactory = (id: SpeakingProviderId) => SpeakingAgentProvider

export const createSpeakingProvider: SpeakingProviderFactory = (id) => id === 'gemini-live'
  ? new GeminiLiveProvider()
  : new OpenAISpeakingProvider()
