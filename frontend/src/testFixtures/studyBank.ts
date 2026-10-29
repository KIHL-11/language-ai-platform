import type { StudyCandidate, StudyItem } from '../types/studyBank'


const provenance = {
  source_type: 'media' as const,
  library_item_id: 'library-item',
  lesson_id: 'personal-lesson',
  source_url: 'https://www.youtube.com/watch?v=personal',
  media_title: 'Daily lesson',
  sentence_id: 1,
  sentence_text: 'We can figure it out.',
  sentence_translation: '我们可以解决。',
  start: 1,
  end: 3,
}

export const studyCandidates: StudyCandidate[] = [
  {
    candidate_id: 'word-figure', kind: 'word', source_language: 'en', target_language: 'zh-CN',
    text: 'figuring', normalized_text: 'figure', meaning: '解决', lemma: 'figure',
    part_of_speech: 'verb', example: 'We can figure it out.', usage: null,
    quality: 'recommended', quality_reasons: ['clean analyzed word candidate'],
    provenance, already_saved: false, study_item_id: null,
  },
  {
    candidate_id: 'chunk-figure', kind: 'chunk', source_language: 'en', target_language: 'zh-CN',
    text: 'figure it out', normalized_text: 'figure it out', meaning: '解决', lemma: null,
    part_of_speech: null, example: null, usage: 'solve something', quality: 'recommended',
    quality_reasons: ['clean multi-word spoken expression'], provenance,
    already_saved: false, study_item_id: null,
  },
  {
    candidate_id: 'chunk-review', kind: 'chunk', source_language: 'en', target_language: 'zh-CN',
    text: 'To go?', normalized_text: 'to go', meaning: '去？', lemma: null,
    part_of_speech: null, example: null, usage: null, quality: 'review',
    quality_reasons: ['short or potentially fragmentary expression'], provenance,
    already_saved: false, study_item_id: null,
  },
  {
    candidate_id: 'chunk-rejected', kind: 'chunk', source_language: 'en', target_language: 'zh-CN',
    text: 'Ah Ah Ah', normalized_text: 'rejected', meaning: '', lemma: null,
    part_of_speech: null, example: null, usage: null, quality: 'rejected',
    quality_reasons: ['caption noise or no reusable spoken language'], provenance,
    already_saved: false, study_item_id: null,
  },
]

export const studyItem: StudyItem = {
  id: 'study-word', user_id: '123e4567-e89b-42d3-a456-426614174000', kind: 'word',
  source_language: 'en', target_language: 'zh-CN', text: 'figuring',
  normalized_text: 'figure', meaning: '解决', active: true,
  created_at: '2026-10-18T08:00:00Z', updated_at: '2026-10-18T08:00:00Z',
  lemma: 'figure', part_of_speech: 'verb', example: 'We can figure it out.',
  usage: null, note: null, provenance: [provenance],
  revision: 3,
  review: {
    due_at: '2026-10-18T08:00:00Z', interval_seconds: 0, ease_factor: 2.5,
    repetition_count: 0, lapse_count: 0, review_count: 0,
    again_count: 0, hard_count: 0, good_count: 0, easy_count: 0,
    last_reviewed_at: null, last_rating: null,
  },
}
