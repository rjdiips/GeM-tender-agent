// PROTOTYPE variant B: variant A's layout, "refined light" design (neutral greys, one indigo accent, hairlines).
import { useState } from 'react'
import { history, memory, request, understood, type Bid } from './data'
import { daysLeft, IconArchive, IconBrain, IconBuilding, IconCheck, IconClock, IconDownload, IconExternal, IconFile,
  IconHistory, IconPlay, IconPlus, IconQuote, IconSearch, IconSend, IconSparkle, IconUndo } from './icons'
import { useReplay } from './useReplay'

export const name = 'Refined light'

const css = `
.rb{--bg:#fafafa;--panel:#fff;--line:#ececef;--line2:#f3f3f5;--ink:#18181b;--ink2:#3f3f46;--mute:#71717a;--faint:#a1a1aa;
 --acc:#4f46e5;--acc-soft:#eef2ff;--ok:#059669;--ok-soft:#ecfdf5;--warn:#d97706;--warn-soft:#fffbeb;--bad:#dc2626;
 font:13.5px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:var(--ink);background:var(--bg);
 display:grid;grid-template-columns:264px minmax(420px,1fr) minmax(520px,44%);height:100vh;letter-spacing:-.005em}
:where(.rb) *{box-sizing:border-box}:where(.rb) button{font:inherit;color:inherit;cursor:pointer}
.rb-side{background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:14px 12px}
.rb-brand{display:flex;align-items:center;gap:10px;padding:4px 6px 14px;font-weight:600;font-size:14px}
.rb-logo{width:26px;height:26px;border-radius:7px;background:linear-gradient(135deg,#6366f1,#4338ca);display:grid;place-items:center;color:#fff}
.rb-new{display:flex;align-items:center;gap:8px;width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);
 box-shadow:0 1px 2px rgba(16,24,40,.04);transition:.15s}.rb-new:hover{border-color:#d4d4d8;background:var(--bg)}
.rb-kbd{margin-left:auto;font-size:11px;color:var(--faint);border:1px solid var(--line);border-radius:4px;padding:0 5px}
.rb-label{font-size:11px;font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;margin:18px 8px 6px}
.rb-item{padding:7px 8px;border-radius:7px;transition:.12s;cursor:pointer}.rb-item:hover{background:var(--line2)}
.rb-item.on{background:var(--acc-soft)}.rb-item .t{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rb-item .m{font-size:12px;color:var(--mute);display:flex;gap:6px;align-items:center}
.rb-new-badge{background:var(--acc);color:#fff;border-radius:999px;padding:0 6px;font-size:10.5px;font-weight:600}
.rb-user{margin-top:auto;display:flex;align-items:center;gap:10px;padding:10px 8px;border-top:1px solid var(--line)}
.rb-avatar{width:30px;height:30px;border-radius:50%;background:#e0e7ff;color:var(--acc);display:grid;place-items:center;font-weight:600;font-size:12px}
.rb-main{display:flex;flex-direction:column;min-width:0}
.rb-head{height:52px;display:flex;align-items:center;gap:10px;padding:0 22px;border-bottom:1px solid var(--line);background:rgba(250,250,250,.8);backdrop-filter:blur(8px)}
.rb-head h2{font-size:14px;font-weight:600;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rb-ghost{display:flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--panel);border-radius:7px;padding:5px 10px;font-size:12.5px;color:var(--ink2)}
.rb-ghost:hover{background:var(--line2)}
.rb-thread{flex:1;overflow:auto;padding:26px 28px 12px}
.rb-user-msg{margin-left:auto;max-width:440px;background:var(--ink);color:#fff;padding:10px 14px;border-radius:14px 14px 4px 14px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.rb-turn{display:flex;gap:12px;margin-top:22px}
.rb-bot{flex:none;width:28px;height:28px;border-radius:8px;background:var(--acc-soft);color:var(--acc);display:grid;place-items:center}
.rb-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.rb-chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.rb-chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:3px 9px;border-radius:999px;background:var(--line2);color:var(--ink2)}
.rb-chip.acc{background:var(--acc-soft);color:var(--acc)}
.rb-steps{margin:12px 0 0;padding:0;list-style:none}
.rb-steps li{display:flex;gap:10px;align-items:flex-start;padding:5px 0;color:var(--ink2)}
.rb-dot{flex:none;width:18px;height:18px;border-radius:50%;display:grid;place-items:center;margin-top:1px}
.rb-dot.done{background:var(--ok-soft);color:var(--ok)}.rb-dot.run{border:2px solid var(--acc-soft);border-top-color:var(--acc);animation:rbspin .8s linear infinite}
@keyframes rbspin{to{transform:rotate(360deg)}}
.rb-hit{display:flex;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin-top:8px;transition:.15s;cursor:pointer}
.rb-hit:hover{border-color:#c7d2fe;box-shadow:0 2px 8px rgba(79,70,229,.08)}
.rb-hit .bar{width:3px;align-self:stretch;border-radius:3px;background:var(--ok)}
.rb-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--mute)}
.rb-due{margin-left:auto;flex:none;display:inline-flex;gap:4px;align-items:center;font-size:12px;padding:2px 8px;border-radius:6px;background:var(--warn-soft);color:var(--warn)}
.rb-due.ok{background:var(--line2);color:var(--mute)}
.rb-composer{padding:12px 22px 20px}
.rb-sugg{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}
.rb-sugg button{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--ink2)}
.rb-sugg button:hover{border-color:#c7d2fe;color:var(--acc)}
.rb-box{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:8px 8px 8px 14px;box-shadow:0 2px 10px rgba(16,24,40,.05)}
.rb-box:focus-within{border-color:#a5b4fc;box-shadow:0 0 0 4px rgba(99,102,241,.12)}
.rb-box input{flex:1;border:none;outline:none;font:inherit;background:transparent}
.rb-send{width:32px;height:32px;border-radius:9px;border:none;background:var(--acc);color:#fff;display:grid;place-items:center}
.rb-panel{background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;min-width:0}
.rb-ptop{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--line);height:52px}
.rb-seg{display:flex;background:var(--line2);border-radius:8px;padding:3px}
.rb-seg button{border:none;background:none;padding:4px 12px;border-radius:6px;font-size:12.5px;color:var(--mute);display:flex;gap:6px;align-items:center}
.rb-seg button.on{background:var(--panel);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.08);font-weight:500}
.rb-count{font-size:11px;background:var(--line);border-radius:999px;padding:0 6px;color:var(--ink2)}
.rb-primary{margin-left:auto;display:flex;gap:6px;align-items:center;border:none;background:var(--ink);color:#fff;border-radius:7px;padding:6px 11px;font-size:12.5px}
.rb-list{overflow:auto;padding:6px 8px}
.rb-row{display:grid;grid-template-columns:1fr 104px 112px 36px;gap:12px;padding:12px 10px;border-radius:10px;align-items:start;position:relative}
.rb-row:hover{background:var(--bg)}
.rb-row+.rb-row{border-top:1px solid var(--line2)}
.rb-title{font-weight:500;margin:2px 0}
.rb-sub{font-size:12px;color:var(--mute);display:flex;gap:5px;align-items:center}
.rb-why{font-size:12.5px;color:var(--ink2);margin-top:6px}
.rb-link{border:none;background:none;color:var(--acc);padding:0;font-size:12.5px;display:inline-flex;gap:4px;align-items:center}
.rb-k{font-size:11px;color:var(--faint)}.rb-v{font-weight:500}
.rb-icon{width:32px;height:32px;border-radius:8px;border:1px solid var(--line);background:var(--panel);display:grid;place-items:center;color:var(--ink2)}
.rb-icon:hover{background:var(--line2)}
.rb-pop{grid-column:1/-1;background:var(--panel);border:1px solid var(--line);border-radius:12px;
 box-shadow:0 12px 32px rgba(16,24,40,.14);padding:12px}
.rb-q{display:flex;gap:8px;padding:8px 0}.rb-q+.rb-q{border-top:1px solid var(--line2)}
.rb-q svg{flex:none;color:var(--acc);margin-top:2px}
.rb-src{font-size:11.5px;color:var(--mute);margin-top:3px;display:flex;gap:6px;align-items:center}
.rb-verified{color:var(--ok);display:inline-flex;gap:3px;align-items:center}
.rb-files{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;padding-top:10px;border-top:1px solid var(--line2)}
.rb-file{display:inline-flex;gap:5px;align-items:center;font-size:12px;border:1px solid var(--line);border-radius:7px;padding:3px 8px;background:var(--panel)}
.rb-file[disabled]{color:var(--faint);cursor:not-allowed}
.rb-mem{padding:12px 10px;border-radius:10px}.rb-mem+.rb-mem{border-top:1px solid var(--line2)}
.rb-badge{font-size:10.5px;font-weight:600;border-radius:5px;padding:1px 6px;text-transform:uppercase;letter-spacing:.04em}
.rb-badge.stated{background:var(--acc-soft);color:var(--acc)}.rb-badge.learned{background:var(--ok-soft);color:var(--ok)}
`

function Due({ closing }: { closing: string }) {
  const d = daysLeft(closing)
  return <span className={`rb-due ${d > 3 ? 'ok' : ''}`}><IconClock width={12} height={12} />{d <= 0 ? 'today' : `${d}d left`}</span>
}

export default function VariantB() {
  const { lines, shown, running, replay } = useReplay()
  const [tab, setTab] = useState<'results' | 'rejected' | 'memory'>('results')
  const [pop, setPop] = useState<string | null>(null)
  const relevant = shown.filter(b => b.relevant)
  const rejected = shown.filter(b => !b.relevant)
  const rows = tab === 'results' ? relevant : rejected
  return (
    <div className="rb">
      <style>{css}</style>
      <aside className="rb-side">
        <div className="rb-brand"><span className="rb-logo"><IconSearch width={14} height={14} /></span>GeM Tender Agent</div>
        <button className="rb-new"><IconPlus />New search<span className="rb-kbd">⌘K</span></button>
        <div className="rb-label">Recent</div>
        {history.slice(0, 2).map((h, i) => (
          <div key={h.title} className={`rb-item ${i === 0 ? 'on' : ''}`}>
            <div className="t">{h.title}</div>
            <div className="m">{h.when} · {h.found} found {h.isNew ? <span className="rb-new-badge">{h.isNew} new</span> : null}</div>
          </div>
        ))}
        <div className="rb-label">Saved searches</div>
        {history.slice(2).map(h => (
          <div key={h.title} className="rb-item">
            <div className="t">{h.title}</div>
            <div className="m"><IconHistory width={12} height={12} />daily · {h.found} found <span className="rb-new-badge">{h.isNew} new</span></div>
          </div>
        ))}
        <div className="rb-user"><span className="rb-avatar">AS</span><div><div style={{ fontWeight: 500 }}>Asha Sharma</div><div style={{ fontSize: 12, color: 'var(--mute)' }}>{memory.length} things remembered</div></div></div>
      </aside>

      <main className="rb-main">
        <div className="rb-head">
          <h2>Management consultancy from banks</h2>
          <span className="rb-chip">{running ? 'Running…' : 'Completed'}</span>
          <span style={{ flex: 1 }} />
          <button className="rb-ghost" onClick={replay}><IconPlay width={13} height={13} />Replay demo</button>
        </div>
        <div className="rb-thread">
          <div className="rb-user-msg">{request}</div>
          <div className="rb-turn">
            <span className="rb-bot"><IconSparkle /></span>
            <div style={{ flex: 1, minWidth: 0, maxWidth: 620 }}>
              {lines.length > 0 && (
                <div className="rb-card">
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>Here’s what I’m looking for</div>
                  <div style={{ color: 'var(--ink2)' }}>{understood.split('. ')[0]}.</div>
                  <div className="rb-chips">
                    <span className="rb-chip acc"><IconSparkle width={12} height={12} />Management consultancy · banks</span>
                    <span className="rb-chip"><IconClock width={12} height={12} />Published 2–16 Nov</span>
                    <span className="rb-chip">36,104 open bids</span><span className="rb-chip">9 likely from titles</span>
                  </div>
                  <ul className="rb-steps">
                    {lines.slice(1).map((l, i) => (
                      <li key={i}><span className={`rb-dot ${running && i === lines.length - 2 ? 'run' : 'done'}`}>{!(running && i === lines.length - 2) && <IconCheck width={11} height={11} />}</span>{l}</li>
                    ))}
                  </ul>
                </div>
              )}
              {relevant.map(b => (
                <div key={b.bidNumber} className="rb-hit" onClick={() => setTab('results')}>
                  <span className="bar" />
                  <div style={{ minWidth: 0 }}>
                    <div className="rb-mono">{b.bidNumber}</div>
                    <div style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.title}</div>
                  </div>
                  <Due closing={b.closing} />
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="rb-composer">
          <div className="rb-sugg">{['Only central banks', 'Show quarantined', 'Closing this week', 'Save as daily search'].map(s => <button key={s}>{s}</button>)}</div>
          <div className="rb-box"><input placeholder="Ask for tenders, or refine this search…" /><button className="rb-send"><IconSend /></button></div>
        </div>
      </main>

      <section className="rb-panel">
        <div className="rb-ptop">
          <div className="rb-seg">
            <button className={tab === 'results' ? 'on' : ''} onClick={() => setTab('results')}>Relevant <span className="rb-count">{relevant.length}</span></button>
            <button className={tab === 'rejected' ? 'on' : ''} onClick={() => setTab('rejected')}><IconArchive width={13} height={13} />Quarantined <span className="rb-count">{rejected.length}</span></button>
            <button className={tab === 'memory' ? 'on' : ''} onClick={() => setTab('memory')}><IconBrain width={13} height={13} />Memory</button>
          </div>
          {tab === 'results' && <button className="rb-primary"><IconDownload width={14} height={14} />Download all</button>}
        </div>
        <div className="rb-list">
          {tab === 'memory' ? memory.map(m => (
            <div key={m.fact} className="rb-mem">
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><span className={`rb-badge ${m.source}`}>{m.source}</span><span className="rb-k">since {m.since}</span></div>
              <div style={{ fontWeight: 500, margin: '6px 0 2px' }}>{m.fact}</div>
              <div style={{ fontSize: 12.5, color: 'var(--mute)' }}>{m.evidence}</div>
              <div style={{ display: 'flex', gap: 14, marginTop: 6 }}><button className="rb-link">Edit</button><button className="rb-link" style={{ color: 'var(--bad)' }}>Forget</button></div>
            </div>
          )) : rows.map((b: Bid) => (
            <div key={b.bidNumber} className="rb-row">
              <div style={{ minWidth: 0 }}>
                <div className="rb-mono" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>{b.bidNumber}<IconExternal width={11} height={11} /></div>
                <div className="rb-title">{b.title}</div>
                <div className="rb-sub"><IconBuilding width={12} height={12} />{b.buyer.split(' / ')[1]}</div>
                <div className="rb-why">{b.reason}</div>
                {b.evidence.length > 0 && <button className="rb-link" style={{ marginTop: 6 }} onClick={() => setPop(pop === b.bidNumber ? null : b.bidNumber)}><IconQuote width={13} height={13} />{pop === b.bidNumber ? 'Hide evidence' : `Evidence (${b.evidence.length})`}</button>}
              </div>
              <div><div className="rb-k">Closes</div><div className="rb-v">{b.closing}</div><div style={{ marginTop: 6 }}><Due closing={b.closing} /></div></div>
              <div><div className="rb-k">EMD</div><div className="rb-v">{b.emd ?? '—'}</div><div className="rb-k" style={{ marginTop: 6 }}>Turnover</div><div className="rb-v">{b.turnover ?? '—'}</div></div>
              <div>{tab === 'results' ? <button className="rb-icon" title="Download"><IconDownload /></button> : <button className="rb-icon" title="Restore"><IconUndo /></button>}</div>
              {pop === b.bidNumber && (
                <div className="rb-pop">
                  {b.evidence.map(e => (
                    <div key={e.quote} className="rb-q"><IconQuote />
                      <div>“{e.quote}”<div className="rb-src"><IconFile width={12} height={12} />{e.file} · page {e.page}{e.verified && <span className="rb-verified"><IconCheck width={12} height={12} />found in document</span>}</div></div>
                    </div>
                  ))}
                  <div className="rb-files">{b.files.map(f => <button key={f.name} className="rb-file" disabled={f.status !== 'ok'}><IconDownload width={12} height={12} />{f.name} · {f.pages}p{f.status === 'placeholder' ? ' · placeholder' : ''}</button>)}</div>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
