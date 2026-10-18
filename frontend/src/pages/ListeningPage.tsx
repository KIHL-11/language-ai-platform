import { useState } from 'react'

import type { LibraryItem } from '../types/library'
import ListeningHome from './ListeningHome'
import ListeningWorkspace from './ListeningWorkspace'

interface ListeningPageProps {
  initialItem?: LibraryItem | null
  onCloseInitialItem?: () => void
}

export default function ListeningPage({ initialItem = null, onCloseInitialItem }: ListeningPageProps) {
  const [selected, setSelected] = useState<LibraryItem | null>(initialItem)
  if (selected) {
    return <ListeningWorkspace item={selected} onBack={() => {
      setSelected(null)
      onCloseInitialItem?.()
    }} />
  }
  return <ListeningHome onOpen={setSelected} />
}
