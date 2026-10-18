import { personalLesson } from './personalLesson'
import type { LibraryItem, LibraryItemSummary } from '../types/library'

export const libraryItem: LibraryItem = {
  id: 'library-item',
  source_key: 'youtube:personal|en|zh-CN|B2',
  created_at: '2026-10-17T08:00:00Z',
  updated_at: '2026-10-17T09:00:00Z',
  lesson: {
    ...personalLesson,
    audio_url: '/api/library/items/library-item/audio',
  },
  progress: {
    is_training: true,
    stage_index: 3,
    sentence_index: 2,
    completed_stages: ['active_recall', 'blind_listening', 'comprehension'],
    training_order: [...personalLesson.training_order],
    lesson_memory_imported: true,
  },
  persistent_audio: {
    status: 'available',
    filename: 'audio.mp3',
    content_type: 'audio/mpeg',
  },
  listening_progress: {
    last_position_seconds: 1.25,
    current_sentence_id: 2,
    playback_rate: 1,
    source_visible: true,
    translation_visible: true,
    mode: 'listen',
  },
}

export const librarySummary: LibraryItemSummary = {
  id: libraryItem.id,
  lesson_id: libraryItem.lesson.id,
  title: libraryItem.lesson.title,
  source_url: libraryItem.lesson.source_url,
  source_language: libraryItem.lesson.source_language,
  target_language: libraryItem.lesson.target_language,
  level: libraryItem.lesson.level,
  source: libraryItem.lesson.source,
  sentence_count: libraryItem.lesson.sentences.length,
  duration_seconds: 3,
  created_at: libraryItem.created_at,
  updated_at: libraryItem.updated_at,
  progress_status: 'in_progress',
  audio_available: true,
  last_position_seconds: libraryItem.listening_progress.last_position_seconds,
  has_listening_progress: true,
}
