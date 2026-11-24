import { useState } from 'react'
import type { RunView } from '../api/types'
import { clockTime } from '../lib/dates'
import { IconCheck, IconMoon, IconSend, IconShield, IconSparkle, IconStop, IconSun } from './icons'
import { Urgency } from './Urgency'
import type { Theme } from '../theme/useTheme'
import './Thread.css'

export function Thread({ run, notes, pending, now, theme, onToggleTheme, onAsk, onStop, onShowResults }: {
  run: RunView | null; notes: { q: string; a: string }[]; pending: string | null; now: string; theme: Theme; onToggleTheme: () => void
  onAsk: (text: string) => void; onStop: () => void; onShowResults: () => void
}) {
  const running = run?.status === 'running'
  const all = run?.progress ?? []
  const newest = new Map<string, number>()
  all.forEach((p, i) => { if (p.kind === 'count') newest.set(p.phase ?? '', i) })
  // one count line per phase, the newest: reading and the deeper search each keep theirs, above Done
  const progress = all.filter((p, i) => p.kind !== 'count' || newest.get(p.phase ?? '') === i)
  const other = theme === 'dark' ? 'light' : 'dark'
  return (
    <main className="gt-main">
      <header className="gt-head">
        <h1>{run?.title ?? 'New search'}</h1>
        {run && <span className="gt-status"><span className={`gt-live ${running ? 'run' : run.status === 'failed' ? 'fail' : ''}`} />{run.status}</span>}
        <span style={{ flex: 1 }} />
        <button className="gt-icon-btn" aria-label={`Switch to ${other} mode`} title={`Switch to ${other} mode`} onClick={onToggleTheme}>
          {theme === 'dark' ? <IconSun /> : <IconMoon />}
        </button>
      </header>
      <div className="gt-thread">
        {!run && !pending && <p className="gt-empty">Describe the tenders you want, e.g. “AML software tenders published in the last 2 weeks”.</p>}
        {run && <>
          <div className="gt-me-msg">{run.request}</div>
          <div className="gt-turn">
            <span className="gt-bot"><IconSparkle /></span>
            <div style={{ flex: 1, minWidth: 0, maxWidth: 640 }}>
              <div className="gt-glass">
                {run.understood ? <>
                  <div className="gt-meaning">Here’s what I’m looking for</div>
                  <div style={{ color: 'var(--ink2)', marginTop: 4 }}>{run.understood.summary}</div>
                  <div className="gt-tags">
                    <span className="gt-tag acc">{[run.understood.name, ...run.understood.buyers].join(' · ')}</span>
                    <span className="gt-tag">{capitalise(run.understood.window)}</span>
                    <span className="gt-tag">{run.understood.likely} matching titles</span>
                    {run.understood.not_this.slice(0, 3).map(n => <span key={n} className="gt-tag">not: {n}</span>)}
                  </div>
                </> : <div className="gt-eyebrow">Working out what you mean…</div>}
                {progress.length > 0 && (
                  <ol className="gt-log gt-mono" aria-label="Progress">
                    {progress.map((p, i) => {
                      const live = running && i === progress.length - 1
                      return (
                        <li key={`${p.at}-${i}`}>
                          <span className="ts">{clockTime(p.at)}</span>
                          {live ? <span className="gt-spin" aria-label="in progress" /> : <IconCheck width={12} height={12} style={{ color: 'var(--acc)', flex: 'none', marginTop: 3 }} />}
                          <span>{live ? p.text : p.text.replace(/…$/, '.')}</span>
                        </li>
                      )
                    })}
                  </ol>
                )}
              </div>
              {run.results.filter(b => b.relevant).map(b => (
                <button key={b.b_id} className="gt-hit" onClick={onShowResults}>
                  <IconShield width={16} height={16} style={{ color: 'var(--acc)', flex: 'none' }} />
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span className="gt-mono gt-bidno" style={{ display: 'block' }}>{b.bid_number}</span>
                    <span style={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.title}</span>
                  </span>
                  <Urgency closing={b.closing} now={now} />
                </button>
              ))}
            </div>
          </div>
        </>}
        {notes.map((n, i) => (
          <div key={i}>
            <div className="gt-me-msg" style={{ marginTop: 24 }}>{n.q}</div>
            <div className="gt-turn"><span className="gt-bot"><IconSparkle /></span><div className="gt-glass" style={{ maxWidth: 640 }}>{n.a}</div></div>
          </div>
        ))}
        {pending && (
          <div>
            <div className="gt-me-msg" style={run || notes.length ? { marginTop: 24 } : undefined}>{pending}</div>
            <div className="gt-turn" role="status">
              <span className="gt-bot gt-thinking"><IconSparkle /></span>
              <div className="gt-glass gt-reading">
                <span className="gt-dots" aria-hidden="true"><i /><i /><i /></span>Reading your request…
              </div>
            </div>
          </div>
        )}
      </div>
      <Composer onAsk={onAsk} running={running} waiting={pending !== null} onStop={onStop} />
    </main>
  )
}

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** While a run works, the send button becomes its Stop button (results judged so far are kept). */
function Composer({ onAsk, running, waiting, onStop }: { onAsk: (text: string) => void; running: boolean; waiting: boolean; onStop: () => void }) {
  const [text, setText] = useState('')
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t || waiting) return
    onAsk(t)
    setText('')
  }
  return (
    <form className="gt-composer" onSubmit={submit}>
      <div className="gt-box">
        <input aria-label="Ask for tenders" value={text} onChange={e => setText(e.target.value)}
               placeholder="Ask for tenders, or refine — e.g. “only central banks”, “closing this week”" />
        {running
          ? <button className="gt-send gt-send-stop" type="button" aria-label="Stop run" title="Stop this run; what was judged so far is kept"
                    onClick={onStop}><IconStop width={14} height={14} /></button>
          : <button className="gt-send" type="submit" aria-label="Send" disabled={!text.trim() || waiting}><IconSend /></button>}
      </div>
    </form>
  )
}
