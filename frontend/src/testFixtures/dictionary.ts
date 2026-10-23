import type { DictionaryLookup } from '../types/dictionary'


export const dictionaryEntry: DictionaryLookup = {
  query: 'Sentence',
  surface_form: 'Sentence',
  lemma: 'sentence',
  lemma_candidates: ['sentence'],
  language: 'en',
  part_of_speech: 'noun',
  status: 'found',
  pronunciations: [
    {
      ipa: '/ˈsɛntəns/', accent: 'US', source: 'Wiktionary data extracted by Wiktextract/Kaikki',
      source_url: 'https://en.wiktionary.org/wiki/sentence',
      license: null,
      audio_url: 'https://upload.wikimedia.org/example/sentence.mp3',
      audio_available: true, tts_fallback: false,
    },
    {
      ipa: null, accent: 'en-GB', source: 'Microsoft Edge Text-to-Speech',
      source_url: null, audio_url: null, audio_available: true, tts_fallback: true,
      license: null,
    },
  ],
  explanations: {
    monolingual: {
      language: 'en', value: 'A group of words that expresses a complete thought.', status: 'available',
      source: 'Wiktionary data extracted by Wiktextract/Kaikki', source_url: 'https://en.wiktionary.org/wiki/sentence',
      license: 'CC BY-SA 3.0 or GFDL', kind: 'dictionary_definition',
    },
    cross_language: {
      language: 'de', value: 'Satz', status: 'available', source: 'Wiktionary data extracted by Wiktextract/Kaikki',
      source_url: 'https://en.wiktionary.org/wiki/sentence', license: 'CC BY-SA 3.0 or GFDL', kind: 'dictionary_translation',
    },
    chinese: {
      language: 'zh-CN', value: '句子', status: 'available', source: 'Wiktionary data extracted by Wiktextract/Kaikki',
      source_url: 'https://en.wiktionary.org/wiki/sentence', license: 'CC BY-SA 3.0 or GFDL', kind: 'dictionary_translation',
    },
  },
  context: {
    source_sentence: 'Sentence 2.', selection_method: 'source_order',
    contextual_sense: 'A group of words that expresses a complete thought.', contextual_explanation: null,
  },
  inflections: ['sentences'],
  examples: ['This is a sentence.'],
  alternate_senses: [{ definition: 'A judicial punishment.', examples: [], source: 'Wiktionary' }],
  source_attribution: [{
    source: 'Wiktionary data extracted by Wiktextract/Kaikki',
    source_url: 'https://en.wiktionary.org/wiki/sentence',
    license: 'CC BY-SA 3.0 or GFDL',
  }],
}
