// PROTOTYPE variant C: variant A's layout, "midnight pro" design (deep neutrals, glass panels, mono numbers, emerald).
import { useState } from 'react'
import { history, memory, request, understood, type Bid } from '../data'
import { daysLeft, IconArchive, IconBrain, IconCheck, IconClock, IconDownload, IconExternal, IconFile, IconPlay,
  IconMoon, IconPlus, IconQuote, IconSend, IconShield, IconSparkle, IconSun, IconUndo } from '../icons'
import { useReplay } from '../useReplay'

export const name = 'Midnight pro'

const css = `
.rc{--side:linear-gradient(180deg,#0c0c0f,#09090b);--glow:rgba(52,211,153,.07);--me:linear-gradient(135deg,#1f2937,#18181b);--glass:rgba(22,22,26,.72);--pop:#141418;--pop-line:rgba(255,255,255,.1);--pop-shadow:0 24px 60px rgba(0,0,0,.6);--box-shadow:0 8px 30px rgba(0,0,0,.35);--hover:rgba(255,255,255,.02);--scroll:rgba(255,255,255,.08);--on-acc:#052e1f;--acc-line:rgba(52,211,153,.35);
 --bg:#09090b;--panel:#0f0f12;--raise:#16161a;--line:rgba(255,255,255,.07);--line2:rgba(255,255,255,.04);--ink:#ededef;--ink2:#b4b4bb;
 --mute:#7c7c86;--acc:#34d399;--acc-d:rgba(52,211,153,.12);--vi:#a78bfa;--vi-d:rgba(167,139,250,.12);--warn:#fbbf24;--warn-d:rgba(251,191,36,.12);--bad:#f87171;
 font:13.5px/1.55 Inter,ui-sans-serif,system-ui,sans-serif;color:var(--ink);background:var(--bg);
 display:grid;grid-template-columns:252px minmax(420px,1fr) minmax(520px,44%);height:100vh}
:where(.rc) *{box-sizing:border-box}:where(.rc) button{font:inherit;color:inherit;cursor:pointer}
.rc ::-webkit-scrollbar{width:8px}.rc ::-webkit-scrollbar-thumb{background:var(--scroll);border-radius:8px}
.rc-mono{font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px}
.rc-side{border-right:1px solid var(--line);padding:16px 12px;display:flex;flex-direction:column;background:var(--side)}
.rc-brand{display:flex;align-items:center;gap:9px;font-weight:600;padding:2px 6px 16px}
.rc-logo{width:24px;height:24px;border-radius:7px;background:conic-gradient(from 200deg,#34d399,#22d3ee,#a78bfa,#34d399);box-shadow:0 0 18px rgba(52,211,153,.35)}
.rc-new{width:100%;display:flex;gap:8px;align-items:center;padding:8px 10px;border-radius:9px;border:1px solid var(--line);background:var(--raise)}
.rc-new:hover{border-color:rgba(52,211,153,.4)}
.rc-label{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mute);margin:20px 8px 6px}
.rc-item{padding:8px;border-radius:8px;cursor:pointer;border:1px solid transparent}.rc-item:hover{background:var(--line2)}
.rc-item.on{background:var(--raise);border-color:var(--line)}
.rc-item .m{font-size:12px;color:var(--mute)}
.rc-pill{font-size:10.5px;padding:0 6px;border-radius:999px;background:var(--acc-d);color:var(--acc);margin-left:6px}
.rc-main{display:flex;flex-direction:column;min-width:0;background:radial-gradient(900px 300px at 50% -80px,var(--glow),transparent)}
.rc-head{height:54px;display:flex;align-items:center;gap:10px;padding:0 24px;border-bottom:1px solid var(--line)}
.rc-status{display:inline-flex;gap:6px;align-items:center;font-size:12px;color:var(--ink2);padding:3px 9px;border:1px solid var(--line);border-radius:999px}
.rc-live{width:7px;height:7px;border-radius:50%;background:var(--acc);box-shadow:0 0 10px var(--acc)}
.rc-live.run{animation:rcpulse 1s ease-in-out infinite}@keyframes rcpulse{50%{opacity:.3}}
.rc-ghost{display:flex;gap:6px;align-items:center;border:1px solid var(--line);background:transparent;border-radius:8px;padding:5px 10px;font-size:12.5px;color:var(--ink2)}
.rc-ghost:hover{background:var(--raise)}
.rc-thread{flex:1;overflow:auto;padding:28px}
.rc-me{margin-left:auto;max-width:440px;padding:11px 15px;border-radius:14px 14px 4px 14px;background:var(--me);border:1px solid var(--line)}
.rc-turn{display:flex;gap:12px;margin-top:24px}
.rc-bot{flex:none;width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:var(--acc-d);color:var(--acc);border:1px solid rgba(52,211,153,.25)}
.rc-glass{background:var(--glass);border:1px solid var(--line);border-radius:14px;padding:16px;backdrop-filter:blur(10px)}
.rc-meaning{font-size:15px;font-weight:500;letter-spacing:-.01em}
.rc-tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.rc-tag{font-size:11.5px;padding:3px 8px;border-radius:6px;background:var(--line2);border:1px solid var(--line);color:var(--ink2)}
.rc-tag.acc{color:var(--acc);border-color:rgba(52,211,153,.25);background:var(--acc-d)}
.rc-log{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.rc-log div{display:flex;gap:10px;padding:3px 0;color:var(--ink2)}
.rc-log .ts{color:var(--mute);flex:none}
.rc-hit{display:flex;align-items:center;gap:12px;margin-top:10px;padding:11px 14px;border-radius:12px;border:1px solid var(--line);background:var(--panel);cursor:pointer;transition:.15s}
.rc-hit:hover{border-color:var(--acc-line);box-shadow:0 0 0 4px rgba(52,211,153,.06)}
.rc-due{margin-left:auto;flex:none;display:inline-flex;gap:4px;align-items:center;font-size:11.5px;padding:2px 8px;border-radius:6px;color:var(--warn);background:var(--warn-d)}
.rc-due.calm{color:var(--ink2);background:var(--line2)}
.rc-composer{padding:12px 24px 22px}
.rc-box{display:flex;gap:8px;align-items:center;padding:8px 8px 8px 14px;border-radius:14px;border:1px solid var(--line);background:var(--raise);box-shadow:var(--box-shadow)}
.rc-box:focus-within{border-color:rgba(52,211,153,.45)}
.rc-box input{flex:1;background:transparent;border:none;outline:none;color:var(--ink);font:inherit}
.rc-hint{font-size:11px;color:var(--mute)}
.rc-send{width:32px;height:32px;border-radius:9px;border:none;background:var(--acc);color:var(--on-acc);display:grid;place-items:center}
.rc-panel{border-left:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;min-width:0}
.rc-tabs{display:flex;gap:2px;padding:0 14px;border-bottom:1px solid var(--line);height:54px;align-items:flex-end}
.rc-tab{border:none;background:none;padding:10px 12px;color:var(--mute);border-bottom:2px solid transparent;display:flex;gap:6px;align-items:center}
.rc-tab.on{color:var(--ink);border-bottom-color:var(--acc)}
.rc-n{font-size:11px;color:var(--ink2);background:var(--raise);border:1px solid var(--line);border-radius:6px;padding:0 5px}
.rc-zip{margin:0 0 8px auto;align-self:center;display:flex;gap:6px;align-items:center;border:1px solid var(--acc-line);color:var(--acc);background:var(--acc-d);border-radius:8px;padding:5px 10px;font-size:12.5px}
.rc-thead,.rc-tr{display:grid;grid-template-columns:minmax(0,1fr) 96px 120px 34px;gap:14px;padding:0 16px}
.rc-thead{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--mute);padding-top:12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.rc-body{overflow:auto}
.rc-tr{padding-top:14px;padding-bottom:14px;border-bottom:1px solid var(--line);position:relative}
.rc-tr:hover{background:var(--hover)}
.rc-bid{color:var(--vi);display:inline-flex;gap:5px;align-items:center}
.rc-title{margin:3px 0;font-weight:500}
.rc-buyer{font-size:12px;color:var(--mute)}
.rc-why{margin-top:6px;font-size:12.5px;color:var(--ink2)}
.rc-ev{margin-top:7px;border:none;background:none;padding:0;color:var(--acc);font-size:12.5px;display:inline-flex;gap:5px;align-items:center}
.rc-num{font-variant-numeric:tabular-nums}
.rc-k{font-size:11px;color:var(--mute)}
.rc-icon{width:32px;height:32px;display:grid;place-items:center;border-radius:8px;border:1px solid var(--line);background:var(--raise);color:var(--ink2)}
.rc-icon:hover{color:var(--acc);border-color:var(--acc-line)}
.rc-pop{grid-column:1/-1;background:var(--pop);border:1px solid var(--pop-line);border-radius:14px;padding:14px;box-shadow:var(--pop-shadow)}
.rc-quote{border-left:2px solid var(--acc);padding:4px 0 4px 12px;margin:6px 0 12px;color:var(--ink)}
.rc-src{display:flex;gap:6px;align-items:center;font-size:11.5px;color:var(--mute);margin-top:4px}
.rc-ok{color:var(--acc);display:inline-flex;gap:3px;align-items:center}
.rc-files{display:flex;gap:6px;flex-wrap:wrap;padding-top:10px;border-top:1px solid var(--line)}
.rc-file{display:inline-flex;gap:5px;align-items:center;font-size:12px;padding:4px 8px;border-radius:7px;border:1px solid var(--line);background:var(--raise)}
.rc-file[disabled]{opacity:.45;cursor:not-allowed}
.rc-mem{padding:14px 16px;border-bottom:1px solid var(--line)}
.rc-src-tag{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;padding:1px 6px;border-radius:5px}
.rc-src-tag.stated{color:var(--vi);background:var(--vi-d)}.rc-src-tag.learned{color:var(--acc);background:var(--acc-d)}
.rc-a{border:none;background:none;padding:0;color:var(--ink2);font-size:12.5px}.rc-a:hover{color:var(--ink)}
.rc.light{--side:linear-gradient(180deg,#ffffff,#fbfbfc);--glow:rgba(16,185,129,.09);--me:linear-gradient(135deg,#18181b,#27272a);
 --glass:rgba(255,255,255,.78);--pop:#ffffff;--pop-line:#e7e7ea;--pop-shadow:0 18px 44px rgba(24,24,27,.12);--box-shadow:0 6px 24px rgba(24,24,27,.07);
 --hover:#fafafa;--scroll:rgba(0,0,0,.12);--on-acc:#fff;--acc-line:rgba(5,150,105,.35);
 --bg:#f7f7f8;--panel:#ffffff;--raise:#ffffff;--line:#e7e7ea;--line2:#f2f2f4;--ink:#111113;--ink2:#3f3f46;--mute:#71717a;
 --acc:#059669;--acc-d:rgba(16,185,129,.1);--vi:#6d28d9;--vi-d:rgba(124,58,237,.08);--warn:#b45309;--warn-d:#fef3c7;--bad:#dc2626}
.rc.light .rc-me{color:#fafafa;border-color:transparent}
.rc.light .rc-logo{box-shadow:0 0 14px rgba(16,185,129,.25)}
.rc.light .rc-live{box-shadow:0 0 6px var(--acc)}
.rc.light .rc-glass{box-shadow:0 1px 2px rgba(24,24,27,.04)}
.rc.light .rc-hit{box-shadow:0 1px 2px rgba(24,24,27,.04)}
.rc.light .rc-hit:hover{box-shadow:0 0 0 4px rgba(16,185,129,.08)}
.rc-theme{width:30px;height:30px;border-radius:8px;border:1px solid var(--line);background:transparent;display:grid;place-items:center;color:var(--ink2)}
.rc-theme:hover{background:var(--raise)}
`
const times = ['13:40:02', '13:40:09', '13:40:31', '13:41:18', '13:42:05']

function Due({ closing }: { closing: string }) {
  const d = daysLeft(closing)
  return <span className={`rc-due ${d > 3 ? 'calm' : ''}`}><IconClock width={12} height={12} />{d <= 0 ? 'closes today' : `${d}d`}</span>
}

export default function VariantC({ theme: initialTheme = 'dark' }: { theme?: 'dark' | 'light' }) {
  const { lines, shown, running, replay } = useReplay()
  const [theme, setTheme] = useState(initialTheme)
  const [tab, setTab] = useState<'results' | 'rejected' | 'memory'>('results')
  const [pop, setPop] = useState<string | null>(null)
  const relevant = shown.filter(b => b.relevant)
  const rejected = shown.filter(b => !b.relevant)
  const rows = tab === 'results' ? relevant : rejected
  return (
    <div className={`rc ${theme === 'light' ? 'light' : ''}`}>
      <style>{css}</style>
      <aside className="rc-side">
        <div className="rc-brand"><span className="rc-logo" />Tender Agent <span className="rc-mono" style={{ color: 'var(--mute)', marginLeft: 'auto' }}>GeM</span></div>
        <button className="rc-new"><IconPlus />New search</button>
        <div className="rc-label">Runs</div>
        {history.map((h, i) => (
          <div key={h.title} className={`rc-item ${i === 0 ? 'on' : ''}`}>
            <div>{h.title}{h.isNew ? <span className="rc-pill">+{h.isNew}</span> : null}</div>
            <div className="m">{h.when} · {h.found} found</div>
          </div>
        ))}
        <div style={{ marginTop: 'auto', display: 'flex', gap: 10, alignItems: 'center', padding: '12px 8px', borderTop: '1px solid var(--line)' }}>
          <span style={{ width: 28, height: 28, borderRadius: 8, background: 'var(--vi-d)', color: 'var(--vi)', display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 600 }}>AS</span>
          <div><div>Asha Sharma</div><div style={{ fontSize: 12, color: 'var(--mute)' }}>finsurge · {memory.length} memories</div></div>
        </div>
      </aside>

      <main className="rc-main">
        <div className="rc-head">
          <span style={{ fontWeight: 600 }}>Management consultancy from banks</span>
          <span className="rc-status"><span className={`rc-live ${running ? 'run' : ''}`} />{running ? 'running' : 'complete'}</span>
          <span style={{ flex: 1 }} />
          <button className="rc-theme" title={theme === 'light' ? 'Dark mode' : 'Light mode'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <IconMoon /> : <IconSun />}</button>
          <button className="rc-ghost" onClick={replay}><IconPlay width={13} height={13} />Replay</button>
        </div>
        <div className="rc-thread">
          <div className="rc-me">{request}</div>
          <div className="rc-turn">
            <span className="rc-bot"><IconSparkle /></span>
            <div style={{ flex: 1, minWidth: 0, maxWidth: 640 }}>
              {lines.length > 0 && (
                <div className="rc-glass">
                  <div className="rc-k" style={{ textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 4 }}>Understood</div>
                  <div className="rc-meaning">Management consultancy for banks and financial institutions</div>
                  <div style={{ color: 'var(--ink2)', marginTop: 4 }}>{understood.split('. ')[0].replace('Looking for management consultancy for banks / financial institutions ', '')}</div>
                  <div className="rc-tags">
                    <span className="rc-tag acc">published 2–16 Nov</span><span className="rc-tag">36,104 open bids</span>
                    <span className="rc-tag">9 likely by title</span><span className="rc-tag">not: engineering consultancy</span>
                  </div>
                  <div className="rc-log rc-mono">
                    {lines.slice(1).map((l, i) => <div key={i}><span className="ts">{times[i + 1]}</span>{running && i === lines.length - 2 ? '◌' : <IconCheck width={12} height={12} style={{ color: 'var(--acc)', flex: 'none', marginTop: 3 }} />}<span>{l}</span></div>)}
                  </div>
                </div>
              )}
              {relevant.map(b => (
                <div key={b.bidNumber} className="rc-hit" onClick={() => setTab('results')}>
                  <IconShield width={16} height={16} style={{ color: 'var(--acc)', flex: 'none' }} />
                  <div style={{ minWidth: 0 }}>
                    <div className="rc-mono rc-bid">{b.bidNumber}</div>
                    <div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.title}</div>
                  </div>
                  <Due closing={b.closing} />
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="rc-composer">
          <div className="rc-box"><input placeholder="Refine — e.g. “only central banks” or “closing this week”" /><span className="rc-hint rc-mono">⌘↵</span><button className="rc-send"><IconSend /></button></div>
        </div>
      </main>

      <section className="rc-panel">
        <div className="rc-tabs">
          <button className={`rc-tab ${tab === 'results' ? 'on' : ''}`} onClick={() => setTab('results')}>Relevant <span className="rc-n">{relevant.length}</span></button>
          <button className={`rc-tab ${tab === 'rejected' ? 'on' : ''}`} onClick={() => setTab('rejected')}><IconArchive width={13} height={13} />Quarantine <span className="rc-n">{rejected.length}</span></button>
          <button className={`rc-tab ${tab === 'memory' ? 'on' : ''}`} onClick={() => setTab('memory')}><IconBrain width={13} height={13} />Memory</button>
          {tab === 'results' && <button className="rc-zip"><IconDownload width={14} height={14} />.zip</button>}
        </div>
        {tab !== 'memory' && <div className="rc-thead"><span>Tender</span><span>Closes</span><span>EMD · turnover</span><span /></div>}
        <div className="rc-body">
          {tab === 'memory' ? memory.map(m => (
            <div key={m.fact} className="rc-mem">
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><span className={`rc-src-tag ${m.source}`}>{m.source}</span><span className="rc-k">since {m.since}</span></div>
              <div style={{ margin: '6px 0 2px', fontWeight: 500 }}>{m.fact}</div>
              <div style={{ fontSize: 12.5, color: 'var(--mute)' }}>{m.evidence}</div>
              <div style={{ display: 'flex', gap: 14, marginTop: 6 }}><button className="rc-a">Edit</button><button className="rc-a" style={{ color: 'var(--bad)' }}>Forget</button></div>
            </div>
          )) : rows.map((b: Bid) => (
            <div key={b.bidNumber} className="rc-tr">
              <div style={{ minWidth: 0 }}>
                <span className="rc-mono rc-bid">{b.bidNumber}<IconExternal width={11} height={11} /></span>
                <div className="rc-title">{b.title}</div>
                <div className="rc-buyer">{b.buyer}</div>
                <div className="rc-why">{b.reason}</div>
                {b.evidence.length > 0 && <button className="rc-ev" onClick={() => setPop(pop === b.bidNumber ? null : b.bidNumber)}><IconQuote width={13} height={13} />{b.evidence.length} quote{b.evidence.length > 1 ? 's' : ''} · {Math.round(b.confidence * 100)}% sure</button>}
              </div>
              <div className="rc-num"><div className="rc-mono">{b.closing}</div><div style={{ marginTop: 6 }}><Due closing={b.closing} /></div></div>
              <div className="rc-num"><div>{b.emd ?? '—'}</div><div className="rc-k">{b.turnover ? `turnover ${b.turnover}` : ''}</div></div>
              <div>{tab === 'results' ? <button className="rc-icon" title="Download"><IconDownload /></button> : <button className="rc-icon" title="Restore"><IconUndo /></button>}</div>
              {pop === b.bidNumber && (
                <div className="rc-pop">
                  {b.evidence.map(e => (
                    <div key={e.quote} className="rc-quote">“{e.quote}”
                      <div className="rc-src"><IconFile width={12} height={12} />{e.file} · p.{e.page}{e.verified && <span className="rc-ok"><IconCheck width={12} height={12} />verified</span>}</div>
                    </div>
                  ))}
                  <div className="rc-files">{b.files.map(f => <button key={f.name} className="rc-file" disabled={f.status !== 'ok'}><IconDownload width={12} height={12} />{f.name}<span className="rc-mono" style={{ color: 'var(--mute)' }}>{f.pages}p</span></button>)}</div>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
