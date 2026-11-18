import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import ChatPrototype from './chat/ChatPrototype'
import '../index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ChatPrototype />
  </StrictMode>,
)
