import { useState } from 'react'
import type { BidResult, FileInfo, MemoryFactView, RunView } from '../api/types'
import { shortDateTime } from '../lib/dates'
import { money } from '../lib/money'
import { IconArchive, IconBrain, IconCheck, IconDownload, IconExternal, IconFile, IconQuote, IconUndo } from './icons'
import { QueriesBy } from './QueriesBy'
import { Urgency } from './Urgency'
import './ResultsPanel.css'

export type Tab = 'relevant' | 'quarantine' | 'memory'

export function ResultsPanel({ run, busy = false, tab, onTab, now, memory, pendingBids, rowErrors, onRestore, onRule, onUnrule, onForget, onEdit }: {
  run: RunView | null; busy?: boolean; tab: Tab; onTab: (t: Tab) => void; now: string; memory: MemoryFactView[]
  pendingBids?: Set<number>; rowErrors?: Record<number, string>
  onRestore: (b: BidResult) => void; onRule: (b: BidResult, d: 'counts' | 'not') => void; onUnrule: (b: BidResult) => void
  onForget: (f: MemoryFactView) => void; onEdit: (f: MemoryFactView, text: string) => void
}) {
  const results = run?.results ?? []
  const relevant = results.filter(b => b.state === 'relevant')
  const borderline = results.filter(b => b.state === 'borderline')
  const quarantined = results.filter(b => b.state === 'quarantined')
  const rows = tab === 'relevant' ? [...borderline, ...relevant] : quarantined
  const grouped = new Set(results.map(b => b.group)).size > 1
  const tabs: [Tab, React.ReactNode][] = [
    ['relevant', <>Relevant <span className="gt-n">{relevant.length}</span>{borderline.length > 0 && <span className="gt-todecide">{borderline.length} to decide</span>}</>],
    ['quarantine', <><IconArchive width={13} height={13} />Quarantine <span className="gt-n">{quarantined.length}</span></>],
    ['memory', <><IconBrain width={13} height={13} />Memory</>],
  ]
  return (
    <section className="gt-panel" aria-label="Results">
      <div className="gt-tabs" role="tablist">
        {tabs.map(([key, label]) => (
          <button key={key} role="tab" id={`tab-${key}`} aria-selected={tab === key} aria-controls="results-panel"
                  className="gt-tab" onClick={() => onTab(key)}>{label}</button>
        ))}
        {tab === 'relevant' && run && relevant.length > 0 && <a className="gt-zip" href={run.zip_url}><IconDownload width={14} height={14} />.zip</a>}
      </div>
      <div className="gt-body" role="tabpanel" id="results-panel" aria-labelledby={`tab-${tab}`}>
        {tab === 'memory' ? <MemoryList facts={memory} onForget={onForget} onEdit={onEdit} /> : <>
          <div className="gt-thead"><span>Tender</span><span>Closes</span><span>EMD · turnover</span><span /></div>
          {rows.length === 0 && busy && tab === 'relevant' && <Searching />}
          {rows.length === 0 && !(busy && tab === 'relevant') && <div className="gt-none">{tab === 'relevant' ? 'No relevant tenders yet.' : 'Nothing quarantined. Bids ruled out land here with the reason, and can be restored.'}</div>}
          {tab === 'relevant' && borderline.length > 0 && <h3 className="gt-section">Borderline: your call</h3>}
          {tab === 'relevant' && borderline.map(b => <ResultRow key={b.b_id} bid={b} now={now} grouped={grouped} quarantined={false} pending={pendingBids?.has(b.b_id) ?? false} error={rowErrors?.[b.b_id]} onRestore={onRestore} onRule={onRule} onUnrule={onUnrule} />)}
          {tab === 'relevant' && borderline.length > 0 && relevant.length > 0 && <h3 className="gt-section">Relevant</h3>}
          {(tab === 'relevant' ? relevant : quarantined).map(b => <ResultRow key={b.b_id} bid={b} now={now} grouped={grouped} quarantined={tab === 'quarantine'} pending={pendingBids?.has(b.b_id) ?? false} error={rowErrors?.[b.b_id]} onRestore={onRestore} onRule={onRule} onUnrule={onUnrule} />)}
        </>}
      </div>
    </section>
  )
}

/** Placeholder cards while a search runs and nothing relevant has been judged yet. */
function Searching() {
  return <div role="status" aria-label="Searching">
    <div className="gt-none gt-searching">Searching… tenders appear here as each one is judged.</div>
    {[0, 1, 2].map(i => (
      <div key={i} className="gt-tr gt-skel" aria-hidden="true">
        <div><i style={{ width: '38%' }} /><i style={{ width: '88%', height: 13 }} /><i style={{ width: '64%' }} /><i style={{ width: '46%' }} /></div>
        <div><i style={{ width: 64 }} /></div><div><i style={{ width: 52 }} /></div><div />
      </div>
    ))}
  </div>
}

function fileTip(f: FileInfo): string {
  return [f.name, f.title && f.title !== f.name ? f.title : '', f.also.length ? `also uploaded as: ${f.also.join(', ')}` : '',
    `file on GeM: ${f.filename}`].filter(Boolean).join('\n')
}

// one line: a long name ends in "…" (the full name is in the tooltip), but "(version 2)" is never cut off
function fileLabel(name: string) {
  const m = name.match(/^(.*?)( \(version \d+\))$/)
  return m ? <><span className="gt-file-name">{m[1]}</span><span>{m[2]}</span></> : <span className="gt-file-name">{name}</span>
}

const OFF_STATUS: Record<string, string> = {
  placeholder: 'placeholder, no content', empty: 'empty on GeM', failed: 'could not download', unsupported: "can't be read",
}

// what the quotes prove, in place of the model's own confidence (it said 85% or more for every verdict it gave)
const PROOF: Record<string, string> = { documents: 'from the documents', title: 'title only', none: 'none found in the documents' }
const PROOF_TIP: Record<string, string> = {
  documents: 'At least one quote was found word for word in an attachment or in the terms the buyer added',
  title: "The quotes only repeat the tender's title and listing details; nothing beyond them was found",
  none: 'None of the quotes could be found in the documents',
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

function ResultRow({ bid, now, grouped, quarantined, pending, error, onRestore, onRule, onUnrule }: {
  bid: BidResult; now: string; grouped: boolean; quarantined: boolean; pending: boolean; error?: string
  onRestore: (b: BidResult) => void; onRule: (b: BidResult, d: 'counts' | 'not') => void; onUnrule: (b: BidResult) => void
}) {
  const [open, setOpen] = useState(false)
  const n = bid.evidence.length
  const mainFile = bid.files.find(f => f.status === 'ok')
  const canOpen = n > 0 || bid.files.length > 0
  // the whole card opens it; links, buttons, the open box and selecting text do not
  const onCard = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    if (!canOpen || target.closest('a, button, input, .gt-evbox') || window.getSelection()?.toString()) return
    setOpen(o => !o)
  }
  return (
    <div className={`gt-tr ${canOpen ? 'can-open' : ''} ${open ? 'is-open' : ''}`} onClick={onCard}>
      <div style={{ minWidth: 0 }}>
        <a className="gt-mono gt-bid" href={bid.gem_url} target="_blank" rel="noreferrer">{bid.bid_number}<IconExternal width={11} height={11} /></a>
        <span className="gt-badges">
          {grouped && <span className="gt-badge ok">{bid.group}</span>}
          {bid.restored && <span className="gt-badge ok">restored</span>}
          {bid.error && <span className="gt-badge" title="Its documents could not be fetched, so it was not judged">not read</span>}
          {bid.needs_review && <span className="gt-badge" title="No quote could be found in the documents beyond the tender's title: read them before relying on this">check quotes</span>}
        </span>
        <div className="gt-title">{bid.title}</div>
        <div className="gt-buyer" title={bid.buyer}>{bid.buyer_org ?? bid.buyer.split(' / ').pop()}</div>
        <div className="gt-why">{bid.reason}</div>
        {bid.state === 'borderline' && (
          <div className="gt-ask">
            <div className="gt-question">{bid.question}</div>
            <div className="gt-rule-btns">
              <button className="gt-btn" disabled={pending} onClick={() => onRule(bid, 'counts')}>Counts for me</button>
              <button className="gt-btn" disabled={pending} onClick={() => onRule(bid, 'not')}>Doesn't count</button>
            </div>
          </div>
        )}
        {bid.ruling && (
          <div className="gt-ruling">
            {/* a "doesn't count" ruling is already the row's reason ("Your ruling: …"), so only the undo is added */}
            {bid.ruling.decision !== 'not' && <>{bid.ruling.by === 'earlier' ? 'decided by your earlier ruling' : 'you ruled'}: "{bid.ruling.fact}"{' '}</>}
            <button className="gt-a" disabled={pending} onClick={() => onUnrule(bid)}>undo</button>
          </div>
        )}
        {error && <div role="alert" style={{ color: 'var(--bad)', fontSize: 12.5, marginTop: 6 }}>{error}</div>}
        {canOpen && (
          <button className="gt-ev" aria-expanded={open} onClick={() => setOpen(!open)}>
            {n > 0
              ? <><IconQuote width={13} height={13} />{plural(n, 'quote')} · <span className={`gt-proof ${bid.proof ?? ''}`}
                  title={PROOF_TIP[bid.proof ?? 'documents']}>{PROOF[bid.proof ?? 'documents']}</span></>
              : bid.stage === 'triage' && <span className="gt-k">decided from the title and first pages</span>}
            {n > 0 || bid.stage === 'triage' ? <span className="gt-sep">·</span> : null}
            <IconFile width={13} height={13} />{plural(bid.files.length, 'file')}
          </button>
        )}
      </div>
      <div className="gt-num"><div className="gt-mono">{shortDateTime(bid.closing)}</div><div style={{ marginTop: 6 }}><Urgency closing={bid.closing} now={now} /></div>{bid.queries && <QueriesBy queries={bid.queries} now={now} />}</div>
      <div className="gt-num"><div>{money(bid.key_facts?.emd)}</div><div className="gt-k">{bid.key_facts?.min_turnover ? `turnover ${bid.key_facts.min_turnover}` : ''}</div></div>
      <div>
        {quarantined
          ? <button className="gt-icon-btn" aria-label={`Restore ${bid.bid_number}`} title="Restore" disabled={pending} onClick={() => onRestore(bid)}><IconUndo /></button>
          : mainFile && <a className="gt-icon-btn" href={mainFile.url} download={mainFile.download_name} aria-label={`Download ${mainFile.name}`} title={`Download ${mainFile.name}`}><IconDownload /></a>}
      </div>
      {open && (
        <div className="gt-evbox" role="region" aria-label={`Quotes and files for ${bid.bid_number}`}>
          {bid.evidence.map((e, i) => (
            <div key={`${i}-${e.file}-${e.page}`} className={`gt-quote ${e.verified ? '' : 'unverified'}`}>“{e.quote}”
              <div className="gt-src" title={`${e.file_name || e.file}, page ${e.page}`}><IconFile width={12} height={12} />
                <span className="gt-src-name">{e.file_name || e.file}</span><span>· p.{e.page}</span>
                {e.verified ? <span className="gt-ok"><IconCheck width={12} height={12} />verified</span>
                  : e.check === 'short'
                    ? <span className="gt-unver" title="Under 4 words: too little to prove anything, so it is not checked or counted">too short to count</span>
                    : <span className="gt-unver" title="These words were not found in any of the tender's documents">not found in documents</span>}
              </div>
            </div>
          ))}
          <div className={`gt-files ${n ? '' : 'alone'}`}>
            {bid.files.map((f, i) => f.status === 'ok'
              ? <a key={`${i}-${f.url}`} className="gt-file" href={f.url} download={f.download_name} title={fileTip(f)}>
                  <IconDownload width={12} height={12} />{fileLabel(f.name)}
                  {f.copies > 1 && <span className="gt-mono" style={{ color: 'var(--mute)' }}>×{f.copies}</span>}
                  <span className="gt-mono" style={{ color: 'var(--mute)' }}>{f.pages}p</span></a>
              : <span key={`${i}-${f.url}`} className="gt-file off" title={fileTip(f)}>
                  <IconFile width={12} height={12} />{fileLabel(f.name)}
                  <span style={{ color: 'var(--mute)' }}>{OFF_STATUS[f.status] ?? f.status}</span></span>)}
          </div>
        </div>
      )}
    </div>
  )
}

function MemoryList({ facts, onForget, onEdit }: { facts: MemoryFactView[]; onForget: (f: MemoryFactView) => void; onEdit: (f: MemoryFactView, t: string) => void }) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  if (facts.length === 0) return <div className="gt-none">Nothing remembered yet. I learn from what you keep, download and restore.</div>
  return <>
    {facts.map(f => (
      <div key={f.id} className="gt-mem">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><span className={`gt-srctag ${f.source}`}>{f.source}</span>{f.kind === 'ruling' && <span className="gt-srctag ruling">ruling · {f.topic}</span>}<span className="gt-k">since {f.since}</span></div>
        {editing === f.id
          ? <form onSubmit={e => { e.preventDefault(); onEdit(f, draft); setEditing(null) }}>
              <input className="gt-edit" aria-label={`Edit: ${f.fact}`} value={draft} onChange={e => setDraft(e.target.value)} autoFocus />
            </form>
          : <div style={{ margin: '6px 0 2px', fontWeight: 500 }}>{f.fact}</div>}
        <div style={{ fontSize: 12.5, color: 'var(--mute)' }}>{f.evidence}</div>
        <div style={{ display: 'flex', gap: 14, marginTop: 6 }}>
          <button className="gt-a" onClick={() => { setEditing(f.id); setDraft(f.fact) }}>Edit</button>
          <button className="gt-a danger" aria-label={`Forget: ${f.fact}`} onClick={() => onForget(f)}>Forget</button>
        </div>
      </div>
    ))}
  </>
}
