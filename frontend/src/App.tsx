import { useState } from 'react'

import LessonGenerator from './pages/LessonGenerator'
import LibraryPage from './pages/LibraryPage'
import ListeningPage from './pages/ListeningPage'
import StudyBankPage from './pages/StudyBankPage'
import DailyReviewPage from './pages/DailyReviewPage'
import ShadowingPage from './pages/ShadowingPage'
import SpeakingPage from './pages/SpeakingPage'
import TodayPage from './pages/TodayPage'
import type { SpeakingLaunch } from './pages/SpeakingPage'
import { getLibraryItem } from './api/library'
import { createPersonalSession, savePersonalSession } from './session/personalSession'
import type { LibraryItem } from './types/library'

function App() {
  const [view, setView] = useState<'today' | 'learn' | 'listening' | 'shadowing' | 'speaking' | 'review' | 'library' | 'study-bank'>('learn')
  const [lessonRevision, setLessonRevision] = useState(0)
  const [listeningItem, setListeningItem] = useState<LibraryItem | null>(null)
  const [shadowingItem, setShadowingItem] = useState<LibraryItem | null>(null)
  const [shadowingSentenceId, setShadowingSentenceId] = useState<number | null>(null)
  const [speakingLaunch, setSpeakingLaunch] = useState<SpeakingLaunch | null>(null)
  const [navigationError, setNavigationError] = useState<string | null>(null)

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

  function openListening(item: LibraryItem) {
    setListeningItem(item)
    setView('listening')
  }

  function openShadowing(item: LibraryItem, sentenceId: number | null = null) {
    setShadowingItem(item)
    setShadowingSentenceId(sentenceId)
    setView('shadowing')
  }

  async function openListeningById(itemId: string) {
    try { openListening(await getLibraryItem(itemId)); setNavigationError(null) }
    catch { setNavigationError('That saved media item is no longer available.') }
  }

  async function openShadowingById(itemId: string, sentenceId: number) {
    try { openShadowing(await getLibraryItem(itemId), sentenceId); setNavigationError(null) }
    catch { setNavigationError('That Shadowing source is no longer available.') }
  }

  function openSpeaking(language: 'en' | 'de', targetIds: string[]) {
    setSpeakingLaunch({ language, targetIds })
    setView('speaking')
  }

  return (
    <div className="min-h-screen bg-[#f3f7f5] font-['Aptos','Segoe_UI',sans-serif] text-[#18211f]">
      <header className="border-b border-[#ccd6d1] bg-white">
        <div className="mx-auto flex min-h-16 max-w-5xl items-center justify-between gap-5 px-5 py-3 sm:px-8">
          <span className="text-sm font-semibold text-[#176b5b]">Language AI</span>
          <nav aria-label="Primary" className="flex flex-wrap justify-end gap-2">
            <button type="button" aria-current={view === 'today' ? 'page' : undefined} onClick={() => setView('today')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Today</button>
            <button type="button" aria-current={view === 'learn' ? 'page' : undefined} onClick={() => setView('learn')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Learn</button>
            <button type="button" aria-current={view === 'listening' ? 'page' : undefined} onClick={() => { setListeningItem(null); setView('listening') }} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Listening</button>
            <button type="button" aria-current={view === 'shadowing' ? 'page' : undefined} onClick={() => { setShadowingItem(null); setShadowingSentenceId(null); setView('shadowing') }} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Shadowing</button>
            <button type="button" aria-current={view === 'speaking' ? 'page' : undefined} onClick={() => { setSpeakingLaunch(null); setView('speaking') }} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Speaking</button>
            <button type="button" aria-current={view === 'review' ? 'page' : undefined} onClick={() => setView('review')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Daily Review</button>
            <button type="button" aria-current={view === 'library' ? 'page' : undefined} onClick={() => setView('library')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">My Library</button>
            <button type="button" aria-current={view === 'study-bank' ? 'page' : undefined} onClick={() => setView('study-bank')} className="h-10 px-4 aria-[current=page]:bg-[#176b5b] aria-[current=page]:text-white">Words & Chunks</button>
          </nav>
        </div>
      </header>
      {navigationError && <p role="alert" className="mx-auto mt-4 max-w-5xl px-5 text-[#8f1d14]">{navigationError}</p>}
      {view === 'today' ? (
        <TodayPage onReview={() => setView('review')} onSpeak={openSpeaking} onListen={(id) => void openListeningById(id)} onShadow={(id, sentenceId) => void openShadowingById(id, sentenceId)} />
      ) : view === 'learn' ? (
        <LessonGenerator key={lessonRevision} />
      ) : view === 'listening' ? (
        <ListeningPage key={listeningItem?.id ?? 'listening-home'} initialItem={listeningItem} onCloseInitialItem={() => setListeningItem(null)} onShadowing={openShadowing} />
      ) : view === 'shadowing' ? (
        <ShadowingPage key={`${shadowingItem?.id ?? 'shadowing-home'}:${shadowingSentenceId ?? ''}`} initialItem={shadowingItem} initialSentenceId={shadowingSentenceId} onCloseInitialItem={() => { setShadowingItem(null); setShadowingSentenceId(null) }} />
      ) : view === 'speaking' ? (
        <SpeakingPage key={`${speakingLaunch?.language ?? 'default'}:${speakingLaunch?.targetIds.join(',') ?? ''}`} initialLaunch={speakingLaunch} />
      ) : view === 'review' ? (
        <DailyReviewPage onSpeak={openSpeaking} />
      ) : view === 'library' ? (
        <LibraryPage onOpen={openLibraryItem} onListen={openListening} onShadowing={openShadowing} />
      ) : (
        <StudyBankPage />
      )}
    </div>
  )
}

export default App
