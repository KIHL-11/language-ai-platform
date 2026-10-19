import { useState } from 'react'

import type { LibraryItem } from '../types/library'
import ShadowingHome from './ShadowingHome'
import ShadowingWorkspace from './ShadowingWorkspace'


interface Props {
  initialItem?: LibraryItem | null
  initialSentenceId?: number | null
  onCloseInitialItem?: () => void
}

export default function ShadowingPage({ initialItem = null, initialSentenceId = null, onCloseInitialItem }: Props) {
  const [selected, setSelected] = useState<LibraryItem | null>(initialItem)
  if (selected) return <ShadowingWorkspace item={selected} initialSentenceId={initialSentenceId} onBack={() => { setSelected(null); onCloseInitialItem?.() }} />
  return <ShadowingHome onOpen={setSelected} />
}
