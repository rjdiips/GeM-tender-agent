// GeM tender agent web chat: runs sidebar | chat thread | results panel, "Midnight pro" in dark and light.
import { useCallback, useEffect, useRef, useState } from 'react'
import { SignInRequired, type BidResult, type MemoryFactView, type RunEvent, type RunListItem, type RunView, type TenderApi, type UserView } from './api/types'
import { SignIn } from './components/SignIn'
import { ResultsPanel, type Tab } from './components/ResultsPanel'
import { Sidebar } from './components/Sidebar'
import { Thread } from './components/Thread'
import { useTheme } from './theme/useTheme'
import './theme/theme.css'

function applyEvent(run: RunView | null, e: RunEvent): RunView | null {
  if (e.type === 'started') return e.run
  if (!run) return run
  switch (e.type) {
    case 'understood': return { ...run, understood: e.understood }
    case 'progress': return { ...run, progress: [...run.progress, e.event] }  // kept whole: a finished run rolls up by phase
    case 'result': return { ...run, results: [...run.results.filter(b => b.b_id !== e.bid.b_id), e.bid] }
    case 'done': return { ...run, status: e.status }
    default: return run
  }
}

export default function App({ api, now, initialRunId, pollMs = 3000 }: {
  api: TenderApi; now: () => string; initialRunId?: string; pollMs?: number
}) {
  const { theme, toggle } = useTheme()
  const [user, setUser] = useState<UserView | null>(null)
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [run, setRun] = useState<RunView | null>(null)
  const [memory, setMemory] = useState<MemoryFactView[]>([])
  const [tab, setTab] = useState<Tab>('relevant')
  const [needsSignIn, setNeedsSignIn] = useState(false)
  const [notes, setNotes] = useState<{ q: string; a: string }[]>([])
  const [loaded, setLoaded] = useState(0)
  // the request just sent, shown at once while the model reads it (nothing comes back until it has)
  const [pending, setPending] = useState<string | null>(null)
  const stop = useRef<(() => void) | null>(null)

  const open = useCallback(async (runId: string) => {
    stop.current?.()
    setPending(null)
    setRun(await api.getRun(runId))
    setRowErrors({})
    setNotes([])
    setTab('relevant')
  }, [api])

  useEffect(() => {
    let live = true
    api.me().then(async me => {
      if (!live) return
      setUser(me)
      setNeedsSignIn(false)
      api.listMemory().then(setMemory)
      const list = await api.listRuns()
      setRuns(list)
      const first = initialRunId ?? list[0]?.run_id
      if (first) void open(first)
    }).catch(err => { if (err instanceof SignInRequired) setNeedsSignIn(true) })
    return () => { live = false; stop.current?.() }
  }, [api, initialRunId, open, loaded])

  // Safety net: while a run is running, fetch its stored state now and then. If the event stream drops (proxy
  // hiccup, laptop asleep, page left open), the panel still catches up without a refresh.
  useEffect(() => {
    if (!run || run.status !== 'running') return
    const id = run.run_id
    const timer = setInterval(() => {
      api.getRun(id).then(fresh => setRun(cur => (cur && cur.run_id === id && cur.status === 'running' ? fresh : cur)))
        .catch(() => undefined)
    }, pollMs)
    return () => clearInterval(timer)
  }, [api, pollMs, run?.run_id, run?.status])  // eslint-disable-line react-hooks/exhaustive-deps

  const ask = (text: string) => {
    stop.current?.()
    setPending(text)
    stop.current = api.ask(text, e => {
      if (e.type === 'show') { setTab(e.tab); return }
      setPending(null)
      if (e.type === 'message') { setNotes(n => [...n, { q: text, a: e.text }]); return }
      if (e.type === 'started') setNotes([])
      setRun(r => applyEvent(r, e))
      if (e.type === 'started') {
        setTab('relevant')
        setRuns(list => list.some(x => x.run_id === e.run.run_id) ? list
          : [{ run_id: e.run.run_id, title: e.run.title, when: now(), found: 0, new_count: 0, saved_search: false }, ...list])
      }
    }, run?.run_id)
  }

  const stopRun = async () => {
    if (!run) return
    await api.stop(run.run_id)
  }

  // bids whose ruling, undo or restore is on its way (their buttons are disabled), and the last failure per bid
  const [pendingBids, setPendingBids] = useState<Set<number>>(new Set())
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  // the bids a ruling or a restore changed replace their rows, keyed by b_id, if that run is still the one open
  const act = async (b: BidResult, call: (runId: string) => Promise<BidResult[]>) => {
    if (!run || pendingBids.has(b.b_id)) return
    const runId = run.run_id
    setPendingBids(p => new Set(p).add(b.b_id))
    setRowErrors(errs => {
      const next = { ...errs }
      delete next[b.b_id]
      return next
    })
    try {
      const changed = await call(runId)
      setRun(cur => cur && cur.run_id === runId ? {
        ...cur, results: cur.results.map(x => changed.find(c => c.b_id === x.b_id) ?? x),
      } : cur)
      api.listMemory().then(setMemory).catch(() => undefined)  // a ruling adds, replaces or removes a fact
    } catch {
      setRowErrors(e => ({ ...e, [b.b_id]: "Couldn't save that. Try again." }))
    } finally {
      setPendingBids(p => { const next = new Set(p); next.delete(b.b_id); return next })
    }
  }
  const restore = (b: BidResult) => act(b, id => api.restore(id, b.b_id))
  const rule = (b: BidResult, d: 'counts' | 'not') => act(b, id => api.rule(id, b.b_id, d))
  const unrule = (b: BidResult) => act(b, id => api.unrule(id, b.b_id))

  const forget = async (f: MemoryFactView) => {
    await api.forgetMemory(f.id)
    setMemory(m => m.filter(x => x.id !== f.id))
  }

  const edit = async (f: MemoryFactView, text: string) => {
    await api.editMemory(f.id, text)
    setMemory(m => m.map(x => x.id === f.id ? { ...x, fact: text, source: 'stated' } : x))
  }

  if (needsSignIn) return <SignIn theme={theme} onSignIn={async name => { await api.login(name); setLoaded(n => n + 1) }} />

  const nowIso = now()
  const found = run?.results.filter(b => b.relevant).length ?? 0
  const sidebarRuns = runs.map(x => (x.run_id === run?.run_id ? { ...x, found } : x))
  return (
    <div className="gt" data-theme={theme} data-testid="app">
      <Sidebar runs={sidebarRuns} currentId={run?.run_id ?? null} user={user} memoryCount={memory.length} now={nowIso}
               onOpen={id => void open(id)} onNew={() => { stop.current?.(); setPending(null); setRun(null) }} />
      <Thread run={run} notes={notes} pending={pending} now={nowIso} theme={theme} onToggleTheme={toggle} onAsk={ask}
              onStop={() => void stopRun()} onShowResults={() => setTab('relevant')} />
      <ResultsPanel run={run} busy={pending !== null || run?.status === 'running'} tab={tab} onTab={setTab} now={nowIso} memory={memory}
                    pendingBids={pendingBids} rowErrors={rowErrors}
                    onRestore={b => void restore(b)} onRule={(b, d) => void rule(b, d)} onUnrule={b => void unrule(b)} onForget={f => void forget(f)} onEdit={(f, t) => void edit(f, t)} />
    </div>
  )
}
