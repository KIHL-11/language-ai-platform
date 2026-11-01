import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { CloudSessionProvider } from './auth/CloudSessionProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CloudSessionProvider><App /></CloudSessionProvider>
  </StrictMode>,
)
