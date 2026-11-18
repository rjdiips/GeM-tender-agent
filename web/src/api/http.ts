// The real TenderApi: the FastAPI backend under /api (gem_agent/app). Sign-in is a cookie set by /api/login.
import { SseParser } from './sse'
import { SignInRequired, type MemoryFactView, type RunEvent, type RunListItem, type RunView, type TenderApi, type UserView } from './types'

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { credentials: 'same-origin', ...init })
  if (r.status === 401) throw new SignInRequired()
  if (!r.ok) throw new Error(`${init?.method ?? 'GET'} ${path}: ${r.status}`)
  return (r.status === 204 ? undefined : await r.json()) as T
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

export class HttpApi implements TenderApi {
  me() { return call<UserView>('/api/me') }
  login(name: string) { return call<UserView>('/api/login', json('POST', { name })) }
  listRuns() { return call<RunListItem[]>('/api/runs') }
  getRun(runId: string) { return call<RunView>(`/api/runs/${runId}`) }
  async stop(runId: string) { await call(`/api/runs/${runId}/stop`, { method: 'POST' }) }
  async restore(runId: string, bId: number) { await call(`/api/runs/${runId}/restore/${bId}`, { method: 'POST' }) }
  listMemory() { return call<MemoryFactView[]>('/api/memory') }
  async forgetMemory(id: string) { await call(`/api/memory/${id}`, { method: 'DELETE' }) }
  async editMemory(id: string, text: string) { await call(`/api/memory/${id}`, json('PATCH', { text })) }

  ask(text: string, onEvent: (e: RunEvent) => void, runId?: string) {
    const abort = new AbortController()
    void (async () => {
      try {
        const r = await fetch('/api/ask', { ...json('POST', { text, run_id: runId ?? null }), signal: abort.signal })
        if (!r.ok || !r.body) throw new Error(`ask: ${r.status}`)
        const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
        const parser = new SseParser()
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          parser.push(value).forEach(onEvent)
        }
      } catch (err) {
        if (!abort.signal.aborted) onEvent({ type: 'message', text: `Something went wrong: ${(err as Error).message}` })
      }
    })()
    return () => abort.abort()
  }
}
