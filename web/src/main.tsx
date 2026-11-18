import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { HttpApi } from './api/http'
import { MockApi } from './api/mock'
import './index.css'

/** IST clock time as an ISO string without zone, matching how the backend sends dates. */
function istNow(): string {
  return new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 19)
}

// The real backend (FastAPI under /api); `VITE_DEMO=1 npm run dev` runs on the demo data instead.
const api = import.meta.env.VITE_DEMO ? new MockApi() : new HttpApi()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App api={api} now={istNow} />
  </StrictMode>,
)
