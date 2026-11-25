// PROTOTYPE variant D: variant A's layout under a branded top bar, "enterprise fintech" design
// (navy sidebar, white canvas, teal accent, elevated cards, KPI strip, status and urgency badges).
import { useState } from 'react'
import { history, memory, request, understood, type Bid } from '../data'
import { daysLeft, IconArchive, IconBrain, IconBuilding, IconCheck, IconClock, IconDownload, IconExternal, IconFile,
  IconHistory, IconPlay, IconPlus, IconQuote, IconSearch, IconSend, IconShield, IconSparkle, IconUndo, IconX } from '../icons'
import { useReplay } from '../useReplay'

export const name = 'Enterprise fintech'

const css = `
.rd{--navy:#0b1f3a;--navy2:#132c4f;--bg:#f4f6fa;--panel:#fff;--line:#e3e8ef;--line2:#eef2f7;--ink:#0f172a;--ink2:#334155;--mute:#64748b;
 --acc:#0d9488;--acc-d:#e6f6f4;--blue:#1d4ed8;--blue-d:#eaf0ff;--warn:#b45309;--warn-d:#fff4e0;--bad:#b91c1c;--bad-d:#fdecec;
 font:13.5px/1.5 Inter,"Segoe UI",ui-sans-serif,system-ui,sans-serif;color:var(--ink);background:var(--bg);height:100vh;display:flex;flex-direction:column}
:where(.rd) *{box-sizing:border-box}:where(.rd) button{font:inherit;color:inherit;cursor:pointer}
.rd-top{height:54px;flex:none;display:flex;align-items:center;gap:16px;padding:0 18px;background:var(--navy);color:#e2e8f0}
.rd-brand{display:flex;align-items:center;gap:10px;font-weight:600;color:#fff;width:232px}
.rd-mark{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,#14b8a6,#0d9488);display:grid;place-items:center;color:#fff;box-shadow:inset 0 -2px 0 rgba(0,0,0,.15)}
.rd-search{flex:1;max-width:520px;display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:6px 10px;color:#94a3b8}
.rd-env{font-size:11px;padding:2px 8px;border-radius:999px;background:rgba(20,184,166,.18);color:#5eead4}
.rd-me{margin-left:auto;display:flex;align-items:center;gap:10px}
.rd-av{width:30px;height:30px;border-radius:50%;background:#14b8a6;color:#fff;display:grid;place-items:center;font-weight:600;font-size:12px}
.rd-body{flex:1;min-height:0;display:grid;grid-template-columns:250px minmax(420px,1fr) minmax(540px,45%)}
.rd-side{background:var(--navy2);color:#cbd5e1;padding:16px 12px;display:flex;flex-direction:column;gap:2px}
.rd-new{display:flex;gap:8px;align-items:center;justify-content:center;border:none;border-radius:8px;padding:9px;background:var(--acc);color:#fff;font-weight:600;margin-bottom:12px;box-shadow:0 2px 6px rgba(13,148,136,.35)}
.rd-sl{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#7f93b0;margin:14px 8px 6px}
.rd-it{padding:9px 10px;border-radius:8px;cursor:pointer;border-left:3px solid transparent}
.rd-it:hover{background:rgba(255,255,255,.05)}.rd-it.on{background:rgba(255,255,255,.09);border-left-color:#2dd4bf;color:#fff}
.rd-it .m{font-size:12px;color:#8ea2bf;display:flex;gap:6px;align-items:center}
.rd-newb{background:#2dd4bf;color:#06302c;border-radius:999px;font-size:10.5px;font-weight:700;padding:0 6px}
.rd-main{display:flex;flex-direction:column;min-width:0;border-right:1px solid var(--line)}
.rd-ctx{display:flex;align-items:center;gap:10px;padding:12px 22px;background:var(--panel);border-bottom:1px solid var(--line)}
.rd-crumb{font-size:12px;color:var(--mute)}.rd-ctx h2{margin:0;font-size:15px;font-weight:600}
.rd-btn{display:flex;gap:6px;align-items:center;border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:6px 11px;font-size:12.5px;color:var(--ink2);box-shadow:0 1px 1px rgba(15,23,42,.04)}
.rd-btn:hover{background:var(--line2)}
.rd-thread{flex:1;overflow:auto;padding:22px}
.rd-q{margin-left:auto;max-width:460px;background:var(--blue);color:#fff;padding:11px 15px;border-radius:12px 12px 3px 12px;box-shadow:0 4px 12px rgba(29,78,216,.2)}
.rd-a{display:flex;gap:12px;margin-top:20px}
.rd-bot{flex:none;width:32px;height:32px;border-radius:10px;background:var(--acc-d);color:var(--acc);display:grid;place-items:center;border:1px solid #c6ebe6}
.rd-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 3px rgba(15,23,42,.06),0 1px 2px rgba(15,23,42,.04);overflow:hidden}
.rd-card-h{padding:12px 16px;border-bottom:1px solid var(--line2);display:flex;gap:8px;align-items:center;font-weight:600}
.rd-card-b{padding:12px 16px}
.rd-facts{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
.rd-fact{background:var(--bg);border-radius:8px;padding:8px 10px}.rd-fact .k{font-size:11px;color:var(--mute)}.rd-fact .v{font-weight:600}
.rd-steps{list-style:none;margin:0;padding:4px 16px 12px}
.rd-steps li{display:flex;gap:10px;padding:6px 0;color:var(--ink2);border-top:1px dashed var(--line2)}
.rd-ck{flex:none;width:20px;height:20px;border-radius:50%;display:grid;place-items:center;background:var(--acc);color:#fff}
.rd-ck.run{background:none;border:2px solid var(--acc-d);border-top-color:var(--acc);animation:rdspin .8s linear infinite}
@keyframes rdspin{to{transform:rotate(360deg)}}
.rd-hit{display:flex;gap:12px;align-items:center;margin-top:10px;padding:12px 14px;border-radius:10px;background:var(--panel);border:1px solid var(--line);box-shadow:0 1px 2px rgba(15,23,42,.04);cursor:pointer}
.rd-hit:hover{border-color:#99d8d0}
.rd-status{flex:none;display:inline-flex;gap:4px;align-items:center;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:6px}
.rd-status.rel{background:var(--acc-d);color:var(--acc)}.rd-status.quar{background:var(--bad-d);color:var(--bad)}
.rd-urg{display:inline-flex;gap:4px;align-items:center;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:6px;background:var(--warn-d);color:var(--warn)}
.rd-urg.calm{background:var(--line2);color:var(--mute);font-weight:500}
.rd-composer{padding:12px 22px 18px;background:var(--panel);border-top:1px solid var(--line)}
.rd-box{display:flex;gap:8px;align-items:center;border:1px solid var(--line);border-radius:10px;padding:6px 6px 6px 12px;background:var(--panel)}
.rd-box:focus-within{border-color:var(--acc);box-shadow:0 0 0 3px rgba(13,148,136,.15)}
.rd-box input{flex:1;border:none;outline:none;font:inherit}
.rd-send{display:flex;gap:6px;align-items:center;border:none;border-radius:8px;padding:7px 12px;background:var(--acc);color:#fff;font-weight:600}
.rd-quick{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.rd-quick button{border:1px solid var(--line);background:var(--bg);border-radius:6px;padding:2px 9px;font-size:12px;color:var(--ink2)}
.rd-panel{display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.rd-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:14px 16px 4px}
.rd-kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.rd-kpi .k{font-size:11.5px;color:var(--mute)}.rd-kpi .v{font-size:20px;font-weight:650;letter-spacing:-.02em}
.rd-tabs{display:flex;align-items:center;gap:4px;padding:10px 16px 0}
.rd-tab{border:none;background:none;padding:8px 12px;border-radius:8px 8px 0 0;color:var(--mute);display:flex;gap:6px;align-items:center;font-weight:500}
.rd-tab.on{background:var(--panel);color:var(--ink);box-shadow:0 -1px 0 var(--line),1px 0 0 var(--line),-1px 0 0 var(--line)}
.rd-tbl{margin:0 16px 16px;background:var(--panel);border:1px solid var(--line);border-radius:0 10px 10px 10px;overflow:auto;flex:1}
.rd-tbl table{width:100%;border-collapse:collapse}
.rd-tbl th{position:sticky;top:0;background:#f8fafc;text-align:left;font-size:11.5px;font-weight:600;color:var(--mute);padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
.rd-tbl td{padding:12px;border-bottom:1px solid var(--line2);vertical-align:top}
.rd-tbl tr:hover td{background:#fafcff}
.rd-bidno{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--blue);display:inline-flex;gap:4px;align-items:center}
.rd-t{font-weight:600;margin:3px 0 2px}.rd-buyer{font-size:12px;color:var(--mute);display:flex;gap:5px;align-items:center}
.rd-why{font-size:12.5px;color:var(--ink2);margin-top:6px}
.rd-evbtn{margin-top:6px;border:none;background:none;padding:0;color:var(--acc);font-weight:600;font-size:12.5px;display:inline-flex;gap:5px;align-items:center}
.rd-money{font-variant-numeric:tabular-nums;font-weight:600}.rd-small{font-size:11.5px;color:var(--mute)}
.rd-act{width:32px;height:32px;border-radius:8px;border:1px solid var(--line);background:var(--panel);display:grid;place-items:center;color:var(--ink2)}
.rd-act:hover{border-color:var(--acc);color:var(--acc)}
.rd-pop{position:fixed;right:28px;top:200px;width:460px;z-index:20;background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:0 24px 48px rgba(15,23,42,.18)}
.rd-pop-h{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line2);font-weight:600}
.rd-pop-b{padding:12px 16px}
.rd-quote{background:var(--bg);border-left:3px solid var(--acc);border-radius:0 8px 8px 0;padding:9px 12px;margin-bottom:10px}
.rd-src{font-size:11.5px;color:var(--mute);margin-top:4px;display:flex;gap:6px;align-items:center}
.rd-ver{color:var(--acc);font-weight:600;display:inline-flex;gap:3px;align-items:center}
.rd-files{display:flex;flex-direction:column;gap:6px;margin-top:6px}
.rd-file{display:flex;gap:8px;align-items:center;border:1px solid var(--line);border-radius:8px;padding:7px 10px;background:var(--panel);font-size:12.5px;text-align:left}
.rd-file[disabled]{color:#94a3b8;cursor:not-allowed}
.rd-mem{padding:14px;border-bottom:1px solid var(--line2)}
.rd-mb{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:2px 7px;border-radius:5px}
.rd-mb.stated{background:var(--blue-d);color:var(--blue)}.rd-mb.learned{background:var(--acc-d);color:var(--acc)}
.rd-lnk{border:none;background:none;padding:0;color:var(--blue);font-weight:500;font-size:12.5px}
`

function Urgency({ closing }: { closing: string }) {
  const d = daysLeft(closing)
  return <span className={`rd-urg ${d > 3 ? 'calm' : ''}`}><IconClock width={12} height={12} />{d <= 0 ? 'Closes today' : `${d} day${d > 1 ? 's' : ''} left`}</span>
}

export default function VariantD() {
  const { lines, shown, running, replay } = useReplay()
  const [tab, setTab] = useState<'results' | 'rejected' | 'memory'>('results')
  const [pop, setPop] = useState<Bid | null>(null)
  const relevant = shown.filter(b => b.relevant)
  const rejected = shown.filter(b => !b.relevant)
  const rows = tab === 'results' ? relevant : rejected
  const nearest = relevant.length ? Math.min(...relevant.map(b => daysLeft(b.closing))) : null
  return (
    <div className="rd">
      <style>{css}</style>
      <header className="rd-top">
        <div className="rd-brand"><span className="rd-mark"><IconShield width={15} height={15} /></span>Tender Intelligence</div>
        <div className="rd-search"><IconSearch width={14} height={14} />Search runs, tenders, buyers…</div>
        <span className="rd-env">GeM · live index 15 min</span>
        <div className="rd-me"><span style={{ fontSize: 12.5 }}>Asha Sharma</span><span className="rd-av">AS</span></div>
      </header>
      <div className="rd-body">
        <aside className="rd-side">
          <button className="rd-new"><IconPlus />New search</button>
          <div className="rd-sl">Recent runs</div>
          {history.slice(0, 2).map((h, i) => (
            <div key={h.title} className={`rd-it ${i === 0 ? 'on' : ''}`}><div>{h.title}</div><div className="m">{h.when} · {h.found} found{h.isNew ? <span className="rd-newb">{h.isNew} new</span> : null}</div></div>
          ))}
          <div className="rd-sl">Saved searches</div>
          {history.slice(2).map(h => (
            <div key={h.title} className="rd-it"><div>{h.title}</div><div className="m"><IconHistory width={12} height={12} />Daily 08:00<span className="rd-newb">{h.isNew} new</span></div></div>
          ))}
        </aside>

        <main className="rd-main">
          <div className="rd-ctx">
            <div><div className="rd-crumb">Runs / Today 13:40</div><h2>Management consultancy from banks</h2></div>
            <span style={{ flex: 1 }} />
            <span className={`rd-status ${running ? '' : 'rel'}`} style={running ? { background: 'var(--blue-d)', color: 'var(--blue)' } : undefined}>{running ? 'In progress' : 'Completed'}</span>
            <button className="rd-btn" onClick={replay}><IconPlay width={13} height={13} />Replay</button>
          </div>
          <div className="rd-thread">
            <div className="rd-q">{request}</div>
            <div className="rd-a">
              <span className="rd-bot"><IconSparkle /></span>
              <div style={{ flex: 1, minWidth: 0, maxWidth: 640 }}>
                {lines.length > 0 && (
                  <div className="rd-card">
                    <div className="rd-card-h"><IconSparkle width={15} height={15} style={{ color: 'var(--acc)' }} />Search understood</div>
                    <div className="rd-card-b">
                      <div style={{ color: 'var(--ink2)' }}>{understood.split('. ')[0]}.</div>
                      <div className="rd-facts">
                        <div className="rd-fact"><div className="k">Window</div><div className="v">Published 2–16 Nov</div></div>
                        <div className="rd-fact"><div className="k">Open bids in window</div><div className="v">36,104</div></div>
                        <div className="rd-fact"><div className="k">Shortlisted</div><div className="v">9</div></div>
                      </div>
                    </div>
                    <ul className="rd-steps">
                      {lines.slice(1).map((l, i) => {
                        const live = running && i === lines.length - 2
                        return <li key={i}><span className={`rd-ck ${live ? 'run' : ''}`}>{!live && <IconCheck width={12} height={12} />}</span>{l}</li>
                      })}
                    </ul>
                  </div>
                )}
                {relevant.map(b => (
                  <div key={b.bidNumber} className="rd-hit" onClick={() => { setTab('results'); setPop(b) }}>
                    <span className="rd-status rel"><IconCheck width={12} height={12} />Relevant</span>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="rd-bidno">{b.bidNumber}</div>
                      <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.title}</div>
                    </div>
                    <Urgency closing={b.closing} />
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="rd-composer">
            <div className="rd-box"><input placeholder="Ask for tenders or refine this run…" /><button className="rd-send"><IconSend width={14} height={14} />Send</button></div>
            <div className="rd-quick">{['Only central banks', 'Closing this week', 'Show quarantined', 'Save as daily search'].map(s => <button key={s}>{s}</button>)}</div>
          </div>
        </main>

        <section className="rd-panel">
          <div className="rd-kpis">
            <div className="rd-kpi"><div className="k">Relevant</div><div className="v" style={{ color: 'var(--acc)' }}>{relevant.length}</div></div>
            <div className="rd-kpi"><div className="k">Quarantined</div><div className="v">{rejected.length}</div></div>
            <div className="rd-kpi"><div className="k">Documents</div><div className="v">31</div></div>
            <div className="rd-kpi"><div className="k">Nearest deadline</div><div className="v" style={{ color: 'var(--warn)' }}>{nearest == null ? '—' : `${nearest}d`}</div></div>
          </div>
          <div className="rd-tabs">
            <button className={`rd-tab ${tab === 'results' ? 'on' : ''}`} onClick={() => setTab('results')}>Relevant ({relevant.length})</button>
            <button className={`rd-tab ${tab === 'rejected' ? 'on' : ''}`} onClick={() => setTab('rejected')}><IconArchive width={13} height={13} />Quarantine ({rejected.length})</button>
            <button className={`rd-tab ${tab === 'memory' ? 'on' : ''}`} onClick={() => setTab('memory')}><IconBrain width={13} height={13} />Memory</button>
            <span style={{ flex: 1 }} />
            {tab === 'results' && <button className="rd-btn" style={{ marginBottom: 6 }}><IconDownload width={14} height={14} />Export zip</button>}
          </div>
          <div className="rd-tbl">
            {tab === 'memory' ? memory.map(m => (
              <div key={m.fact} className="rd-mem">
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><span className={`rd-mb ${m.source}`}>{m.source}</span><span className="rd-small">since {m.since}</span></div>
                <div style={{ fontWeight: 600, margin: '6px 0 2px' }}>{m.fact}</div>
                <div className="rd-small">{m.evidence}</div>
                <div style={{ display: 'flex', gap: 14, marginTop: 6 }}><button className="rd-lnk">Edit</button><button className="rd-lnk" style={{ color: 'var(--bad)' }}>Forget</button></div>
              </div>
            )) : (
              <table>
                <thead><tr><th>Tender</th><th>Status</th><th>Closes</th><th>EMD</th><th>Turnover</th><th /></tr></thead>
                <tbody>
                  {rows.map(b => (
                    <tr key={b.bidNumber}>
                      <td style={{ maxWidth: 300 }}>
                        <span className="rd-bidno">{b.bidNumber}<IconExternal width={11} height={11} /></span>
                        <div className="rd-t">{b.title}</div>
                        <div className="rd-buyer"><IconBuilding width={12} height={12} />{b.buyer.split(' / ')[1]}</div>
                        <div className="rd-why">{b.reason}</div>
                        {b.evidence.length > 0 && <button className="rd-evbtn" onClick={() => setPop(b)}><IconQuote width={13} height={13} />View evidence</button>}
                      </td>
                      <td>{b.relevant ? <span className="rd-status rel">{Math.round(b.confidence * 100)}% match</span> : <span className="rd-status quar">Quarantined</span>}</td>
                      <td style={{ whiteSpace: 'nowrap' }}><div>{b.closing}</div><div style={{ marginTop: 6 }}><Urgency closing={b.closing} /></div></td>
                      <td className="rd-money">{b.emd ?? '—'}</td>
                      <td className="rd-money">{b.turnover ?? '—'}</td>
                      <td>{tab === 'results' ? <button className="rd-act" title="Download"><IconDownload /></button> : <button className="rd-act" title="Restore"><IconUndo /></button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>

      {pop && (
        <div className="rd-pop">
          <div className="rd-pop-h"><IconQuote width={15} height={15} style={{ color: 'var(--acc)' }} />Evidence · <span className="rd-bidno">{pop.bidNumber}</span><span style={{ flex: 1 }} /><button className="rd-act" style={{ width: 28, height: 28 }} onClick={() => setPop(null)}><IconX width={14} height={14} /></button></div>
          <div className="rd-pop-b">
            <div style={{ marginBottom: 10, color: 'var(--ink2)' }}>{pop.reason}</div>
            {pop.evidence.map(e => (
              <div key={e.quote} className="rd-quote">“{e.quote}”<div className="rd-src"><IconFile width={12} height={12} />{e.file} · page {e.page}{e.verified && <span className="rd-ver"><IconCheck width={12} height={12} />Found in document</span>}</div></div>
            ))}
            <div className="rd-small" style={{ fontWeight: 600, margin: '4px 0' }}>Documents</div>
            <div className="rd-files">{pop.files.map(f => <button key={f.name} className="rd-file" disabled={f.status !== 'ok'}><IconFile width={14} height={14} /><span style={{ flex: 1 }}>{f.name}</span><span className="rd-small">{f.pages} pages{f.status === 'placeholder' ? ' · placeholder' : ''}</span>{f.status === 'ok' && <IconDownload width={14} height={14} />}</button>)}</div>
          </div>
        </div>
      )}
    </div>
  )
}
