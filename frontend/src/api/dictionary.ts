import { API_BASE_URL } from './apiBase'
import type { DictionaryLookup, DictionarySelection } from '../types/dictionary'


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function nullableString(value: unknown): boolean {
  return value === null || typeof value === 'string'
}

function isExplanation(value: unknown): boolean {
  return isRecord(value) && typeof value.language === 'string' &&
    nullableString(value.value) && ['available', 'unavailable'].includes(String(value.status)) &&
    nullableString(value.source) && nullableString(value.source_url) && nullableString(value.license) &&
    ['dictionary_definition', 'dictionary_translation'].includes(String(value.kind))
}

function isDictionaryLookup(value: unknown): value is DictionaryLookup {
  return isRecord(value) && typeof value.query === 'string' && typeof value.surface_form === 'string' &&
    nullableString(value.lemma) && (value.language === 'en' || value.language === 'de') &&
    nullableString(value.part_of_speech) &&
    ['found', 'ambiguous', 'not_found', 'not_configured'].includes(String(value.status)) &&
    Array.isArray(value.lemma_candidates) && value.lemma_candidates.every((item) => typeof item === 'string') &&
    Array.isArray(value.pronunciations) && value.pronunciations.every((item) => isRecord(item) &&
      nullableString(item.ipa) && typeof item.accent === 'string' && typeof item.source === 'string' &&
      nullableString(item.source_url) && nullableString(item.license) && nullableString(item.audio_url) &&
      typeof item.audio_available === 'boolean' && typeof item.tts_fallback === 'boolean') &&
    isRecord(value.explanations) && isExplanation(value.explanations.monolingual) &&
    isExplanation(value.explanations.cross_language) && isExplanation(value.explanations.chinese) &&
    isRecord(value.context) && typeof value.context.source_sentence === 'string' &&
    ['lexical_overlap', 'part_of_speech', 'morphology', 'source_order', 'ambiguous', 'unavailable'].includes(String(value.context.selection_method)) &&
    nullableString(value.context.contextual_sense) && nullableString(value.context.contextual_explanation) &&
    Array.isArray(value.inflections) && value.inflections.every((item) => typeof item === 'string') &&
    Array.isArray(value.examples) && value.examples.every((item) => typeof item === 'string') &&
    Array.isArray(value.alternate_senses) && value.alternate_senses.every((item) => isRecord(item) &&
      typeof item.definition === 'string' && Array.isArray(item.examples) &&
      item.examples.every((example) => typeof example === 'string') && typeof item.source === 'string') &&
    Array.isArray(value.source_attribution) && value.source_attribution.every((item) => isRecord(item) &&
      typeof item.source === 'string' && typeof item.source_url === 'string' && typeof item.license === 'string')
}

export async function lookupDictionary(
  selection: DictionarySelection,
  signal?: AbortSignal,
  includeInflections = false,
): Promise<DictionaryLookup> {
  const response = await fetch(`${API_BASE_URL}/api/dictionary/lookup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      language: selection.language,
      word: selection.word,
      sentence: selection.sentence,
      target_language: 'zh-CN',
      include_inflections: includeInflections,
    }),
    signal,
  })
  if (!response.ok) throw new Error(`Dictionary lookup failed (${response.status}).`)
  const value: unknown = await response.json()
  if (!isDictionaryLookup(value)) throw new Error('Dictionary lookup returned an unexpected response.')
  return value
}

export async function synthesizePronunciation(
  text: string,
  accent: 'en-US' | 'en-GB' | 'de-DE',
): Promise<{ audioUrl: string; mime: string }> {
  const response = await fetch(`${API_BASE_URL}/api/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, accent, gender: 'female', rate: 1 }),
  })
  if (!response.ok) throw new Error(`Pronunciation synthesis failed (${response.status}).`)
  const value: unknown = await response.json()
  if (!isRecord(value) || typeof value.audio_b64 !== 'string' || typeof value.mime !== 'string') {
    throw new Error('Pronunciation synthesis returned an unexpected response.')
  }
  return { audioUrl: `data:${value.mime};base64,${value.audio_b64}`, mime: value.mime }
}
