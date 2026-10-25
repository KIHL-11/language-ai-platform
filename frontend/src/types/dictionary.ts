import type { SourceLanguage } from './lesson'

export type DictionaryStatus = 'found' | 'ambiguous' | 'not_found' | 'not_configured'

export interface DictionaryExplanation {
  language: string
  value: string | null
  status: 'available' | 'unavailable'
  source: string | null
  source_url: string | null
  license: string | null
  kind: 'dictionary_definition' | 'dictionary_translation'
}

export interface DictionaryPronunciation {
  ipa: string | null
  accent: string
  source: string
  source_url: string | null
  license: string | null
  audio_url: string | null
  audio_available: boolean
  tts_fallback: boolean
}

export interface DictionaryLookup {
  query: string
  surface_form: string
  lemma: string | null
  lemma_candidates: string[]
  language: SourceLanguage
  part_of_speech: string | null
  status: DictionaryStatus
  pronunciations: DictionaryPronunciation[]
  explanations: {
    monolingual: DictionaryExplanation
    cross_language: DictionaryExplanation
    chinese: DictionaryExplanation
  }
  context: {
    source_sentence: string
    selection_method: 'lexical_overlap' | 'part_of_speech' | 'morphology' | 'source_order' | 'ambiguous' | 'unavailable'
    contextual_sense: string | null
    contextual_explanation: string | null
  }
  inflections: string[]
  inflections_status: 'not_requested' | 'available' | 'index_unavailable' | 'index_stale' | 'index_invalid'
  inflections_message: string | null
  examples: string[]
  alternate_senses: Array<{ definition: string; examples: string[]; source: string }>
  source_attribution: Array<{ source: string; source_url: string; license: string }>
}

export interface DictionarySelection {
  word: string
  sentence: string
  language: SourceLanguage
}
