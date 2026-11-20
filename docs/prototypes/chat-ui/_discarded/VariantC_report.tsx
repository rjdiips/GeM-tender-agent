// PROTOTYPE variant C: each run is a report that grows as it works. Centred reading column, a stage stepper,
// matches as full sections with quoted evidence, quarantine collapsed below, history and memory in a slim rail.
import { useState } from 'react'
import { bids, history, memory, request, understood } from '../data'
import { useReplay } from '../useReplay'

export const name = 'Run as a report'

const c = { bg: '#fbfaf7', ink: '#22201c', mute: '#7a746a', line: '#e7e2d8', accent: '#8a3ffc', ok: '#1b7f4b', card: '#fff' }
const stages = ['Understood', 'Shortlisted', 'Documents', 'Judged']

export default function VariantC() {
  const { lines, shown, running, replay } = useReplay()
  const [drawer, setDrawer] = useState<'history' | 'memory' | null>(null)
  const [showRejected, setShowRejected] = useState(false)
  const stage = Math.min(lines.length, stages.length)
  const relevant = shown.filter(b => b.relevant)
  const rejected = shown.filter(b => !b.relevant)
  return (
    <div style={{ minHeight: '100vh', background: c.bg, color: c.ink, font: '15px Georgia, serif' }}>
      <nav style={{ position: 'fixed', left: 0, top: 0, bottom: 0, width: 56, borderRight: `1px solid ${c.line}`, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, paddingTop: 16, font: '20px system-ui' }}>
        <span title="New">✚</span>
        <span title="History" onClick={() => setDrawer(drawer === 'history' ? null : 'history')} style={{ cursor: 'pointer' }}>🕘</span>
        <span title="Memory" onClick={() => setDrawer(drawer === 'memory' ? null : 'memory')} style={{ cursor: 'pointer' }}>🧠</span>
      </nav>
      {drawer && (
        <div style={{ position: 'fixed', left: 56, top: 0, bottom: 0, width: 300, background: c.card, borderRight: `1px solid ${c.line}`, padding: 18, font: '14px system-ui', zIndex: 3 }}>
          <h4 style={{ marginTop: 0 }}>{drawer === 'history' ? 'Past runs and saved searches' : 'What I remember about you'}</h4>
          {drawer === 'history'
            ? history.map(h => <div key={h.title} style={{ padding: '8px 0', borderTop: `1px solid ${c.line}` }}>{h.title}<div style={{ color: c.mute, fontSize: 12 }}>{h.when} · {h.found} found{h.isNew ? ` · ${h.isNew} new` : ''}</div></div>)
            : memory.map(m => <div key={m.fact} style={{ padding: '8px 0', borderTop: `1px solid ${c.line}` }}>{m.fact}<div style={{ color: c.mute, fontSize: 12 }}>{m.source} · {m.evidence}</div><a href="#" style={{ fontSize: 12 }}>forget</a></div>)}
        </div>
      )}

      <article style={{ maxWidth: 780, margin: '0 auto', padding: '40px 24px 140px 80px' }}>
        <div style={{ color: c.mute, font: '13px system-ui' }}>You asked</div>
        <h1 style={{ fontSize: 28, margin: '4px 0 18px', fontWeight: 400 }}>“{request}”</h1>

        <div style={{ display: 'flex', gap: 0, margin: '0 0 18px', font: '12px system-ui' }}>
          {stages.map((s, i) => (
            <div key={s} style={{ flex: 1, borderTop: `3px solid ${i < stage ? c.accent : c.line}`, paddingTop: 6, color: i < stage ? c.ink : c.mute }}>{i < stage ? '✓ ' : ''}{s}</div>
          ))}
        </div>
        {lines.length > 0 && <p style={{ fontStyle: 'italic', color: c.mute }}>{understood}</p>}
        {running && <p style={{ font: '13px system-ui', color: c.accent }}>{lines[lines.length - 1]}</p>}

        {relevant.length > 0 && <h2 style={{ fontWeight: 400, borderBottom: `1px solid ${c.line}`, paddingBottom: 6 }}>{relevant.length} matching tender{relevant.length > 1 ? 's' : ''}</h2>}
        {relevant.map((b, n) => (
          <section key={b.bidNumber} style={{ margin: '22px 0 30px' }}>
            <div style={{ font: '12px system-ui', color: c.mute }}>{n + 1}. <a href="#">{b.bidNumber}</a> · {b.buyer} · published {b.published} · <b style={{ color: '#b42318' }}>closes {b.closing}</b></div>
            <h3 style={{ margin: '6px 0', fontWeight: 600 }}>{b.title}</h3>
            <p style={{ margin: '6px 0 10px' }}>{b.reason}</p>
            {b.evidence.map(e => (
              <blockquote key={e.quote} style={{ margin: '8px 0', padding: '6px 14px', borderLeft: `3px solid ${c.accent}`, background: '#f3effc' }}>
                {e.quote}<div style={{ font: '11px system-ui', color: c.mute, marginTop: 4 }}>{e.file}, page {e.page} {e.verified ? '· found in the document ✓' : '· not found ⚠'}</div>
              </blockquote>
            ))}
            <div style={{ display: 'flex', gap: 18, font: '13px system-ui', margin: '10px 0' }}>
              <span><b>EMD</b> {b.emd ?? '—'}</span><span><b>Turnover</b> {b.turnover ?? '—'}</span><span><b>Period</b> {b.period ?? '—'}</span>
            </div>
            <div style={{ font: '13px system-ui' }}>{b.files.filter(f => f.status === 'ok').map(f => <a key={f.name} href="#" style={{ marginRight: 14 }}>⬇ {f.name} ({f.pages}p)</a>)}</div>
          </section>
        ))}

        {rejected.length > 0 && (
          <div style={{ borderTop: `1px solid ${c.line}`, paddingTop: 10, font: '14px system-ui' }}>
            <a href="#" onClick={e => { e.preventDefault(); setShowRejected(!showRejected) }}>{showRejected ? '▾' : '▸'} {rejected.length} quarantined (kept 30 days)</a>
            {showRejected && rejected.map(b => (
              <div key={b.bidNumber} style={{ padding: '8px 0', borderBottom: `1px dashed ${c.line}` }}>
                <b>{b.title.slice(0, 70)}</b> <span style={{ color: c.mute }}>— {b.reason}</span> <a href="#">restore</a>
              </div>
            ))}
          </div>
        )}
        {!running && shown.length === bids.length && <p style={{ font: '13px system-ui' }}><a href="#">⬇ Download everything as one zip</a></p>}
      </article>

      <div style={{ position: 'fixed', bottom: 64, left: 56, right: 0, display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: 700, display: 'flex', gap: 8, background: c.card, border: `1px solid ${c.line}`, borderRadius: 14, padding: 8, boxShadow: '0 6px 24px rgba(0,0,0,.08)', font: '14px system-ui' }}>
          <input placeholder="Refine or ask again…" style={{ flex: 1, border: 'none', outline: 'none', padding: 8 }} />
          <button onClick={replay} style={{ border: 'none', background: c.accent, color: '#fff', borderRadius: 10, padding: '0 14px' }}>Replay run</button>
        </div>
      </div>
    </div>
  )
}
