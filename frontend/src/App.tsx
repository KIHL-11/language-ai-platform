import { useState } from 'react'

import LessonGenerator from './pages/LessonGenerator'
import LibraryPage from './pages/LibraryPage'
import { createPersonalSession, savePersonalSession } from './session/personalSession'
import type { LibraryItem } from './types/library'

function App() {
  const [view, setView] = useState<'learn' | 'library'>('learn')
  const [lessonRevision, setLessonRevision] = useState(0)

  function openLibraryItem(item: LibraryItem, startTraining: boolean) {
    const session = {
      ...createPersonalSession(item.lesson),
      isTraining: startTraining,
      lessonMemoryImported: item.progress.lesson_memory_imported,
      trainingState: {
        order: [...item.progress.training_order],
        stageIndex: item.progress.stage_index,
        sentenceIndex: item.progress.sentence_index,
        completedStages: [...item.progress.completed_stages],
        completedSentences: {},
      },
    }
    savePersonalSession(session)
    setLessonRevision((current) => current + 1)
    setView('learn')
  }

  return (
    <div className="min-h-screen bg-[#f3f7f5] font-['Aptos','Segoe_UI',sans-serif] text-[#18211f]">
      <header className="border-b border-[#ccd6d1] bg-white">
        <div className="mx-auto flex min-h-16 max-w-5xl items-center justify-between gap-5 px-5 py-3 sm:px-8">
          <span className="text-sm font-semibold text-[#176b5b]">Language AI</span>
          <nav aria-label="Primary" className="flex gap-2">
            <button type="button" aria-current={view === 'learn' ? 'page' : undefined} onClick={() => setView('learn')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Learn</button>
            <button type="button" aria-current={view === 'library' ? 'page' : undefined} onClick={() => setView('library')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">My Library</button>
          </nav>
        </div>
      </header>
      {view === 'learn' ? (
        <LessonGenerator key={lessonRevision} />
      ) : (
        <LibraryPage onOpen={openLibraryItem} />
      )}
    </div>
  )
}

export default App
