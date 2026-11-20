// PROTOTYPE variant B: results first. A command bar instead of a transcript, a dense expandable table,
// progress as a thin activity feed on the right, history as tabs, memory behind a chip.
import { useState } from 'react'
import { history, memory, request, understood } from '../data'
import { useReplay } from '../useReplay'

export const name = 'Results-first workspace'

const c = { bg: '#0f1419', card: '#161c24', line: '#263040', ink: '#e6edf3', mute: '#8b98a8', accent: '#4f9cf9', ok: '#3fb950', warn: '#d29922' }

export default function VariantB() {
  const { lines, shown, running, replay } = useReplay()
  const [filter, setFilter] = useState<'relevant' | 'quarantined'>('relevant')
  const [open, setOpen] = useState<string | null>('GEM/2026/B/8162911')
  const [showMemory, setShowMemory] = useState(false)
  const rows = shown.filter(b => (filter === 'relevant') === b.relevant)
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: c.bg, color: c.ink, font: '13px ui-sans-serif, system-ui' }}>
      <header style={{ display: 'flex', gap: 4, padding: '8px 16px 0', borderBottom: `1px solid ${c.line}` }}>
        {history.map((h, i) => (
          <div key={h.title} style={{ padding: '8px 14px', borderRadius: '8px 8px 0 0', background: i === 0 ? c.card : 'transparent', color: i === 0 ? c.ink : c.mute }}>
            {h.title} {h.isNew ? <span style={{ background: c.accent, color: '#000', borderRadius: 8, padding: '0 6px', marginLeft: 4 }}>{h.isNew}</span> : null}
          </div>
        ))}
        <span style={{ flex: 1 }} />
        <button onClick={() => setShowMemory(true)} style={{ alignSelf: 'center', background: c.card, color: c.mute, border: `1px solid ${c.line}`, borderRadius: 999, padding: '4px 12px' }}>🧠 What I remember ({memory.length})</button>
      </header>

      <div style={{ padding: '14px 16px', background: c.card, borderBottom: `1px solid ${c.line}` }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <input defaultValue={request} style={{ flex: 1, fontSize: 16, padding: '10px 14px', background: c.bg, color: c.ink, border: `1px solid ${c.line}`, borderRadius: 8 }} />
          <button onClick={replay} style={{ padding: '0 18px', background: c.accent, border: 'none', borderRadius: 8, fontWeight: 700 }}>Search</button>
        </div>
        <div style={{ marginTop: 8, color: c.mute }}>Understood as: <span style={{ color: c.ink }}>{understood.split('. ')[0]}</span> · <a style={{ color: c.accent }} href="#">not what you meant?</a></div>
      </div>

      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 280px', minHeight: 0 }}>
        <main style={{ overflow: 'auto', padding: 16 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
            {(['relevant', 'quarantined'] as const).map(f => (
              <button key={f} onClick={() => setFilter(f)} style={{ padding: '6px 14px', borderRadius: 6, border: `1px solid ${c.line}`, background: filter === f ? c.accent : 'transparent', color: filter === f ? '#000' : c.ink }}>
                {f === 'relevant' ? 'Relevant' : 'Quarantined'} ({shown.filter(b => (f === 'relevant') === b.relevant).length})
              </button>
            ))}
            <span style={{ flex: 1 }} />
            <span style={{ color: c.mute }}>sorted by closing date</span>
            <button style={{ background: 'transparent', color: c.ink, border: `1px solid ${c.line}`, borderRadius: 6, padding: '6px 10px' }}>⬇ zip</button>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ color: c.mute, textAlign: 'left', fontSize: 12 }}><th style={{ width: 18 }} /><th>Closes</th><th>Bid</th><th>Title</th><th>Buyer</th><th>EMD</th><th>Turnover</th><th>Conf.</th></tr></thead>
            <tbody>
              {rows.map(b => [
                <tr key={b.bidNumber} onClick={() => setOpen(open === b.bidNumber ? null : b.bidNumber)} style={{ borderTop: `1px solid ${c.line}`, cursor: 'pointer' }}>
                  <td>{open === b.bidNumber ? '▾' : '▸'}</td>
                  <td style={{ padding: '7px 4px', whiteSpace: 'nowrap', color: c.warn }}>{b.closing}</td>
                  <td style={{ whiteSpace: 'nowrap', color: c.accent }}>{b.bidNumber}</td>
                  <td>{b.title.slice(0, 70)}</td>
                  <td style={{ color: c.mute }}>{b.buyer.split(' / ')[1]}</td>
                  <td>{b.emd ?? '—'}</td><td>{b.turnover ?? '—'}</td>
                  <td>{Math.round(b.confidence * 100)}%</td>
                </tr>,
                open === b.bidNumber && (
                  <tr key={b.bidNumber + 'x'}><td /><td colSpan={7} style={{ padding: '4px 4px 14px' }}>
                    <div style={{ marginBottom: 6 }}><b>{b.relevant ? 'Why it matches' : 'Why it was quarantined'}:</b> {b.reason}</div>
                    {b.evidence.map(e => <div key={e.quote} style={{ borderLeft: `3px solid ${c.ok}`, paddingLeft: 10, margin: '6px 0', color: c.mute }}>“{e.quote}” <span style={{ fontSize: 11 }}>— {e.file} p.{e.page} {e.verified ? '✓' : '⚠'}</span></div>)}
                    <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                      {b.files.map(f => <button key={f.name} disabled={f.status !== 'ok'} style={{ background: c.bg, color: f.status === 'ok' ? c.ink : c.mute, border: `1px solid ${c.line}`, borderRadius: 6, padding: '3px 8px' }}>⬇ {f.name} · {f.pages}p{f.status === 'placeholder' ? ' (placeholder)' : ''}</button>)}
                      {!b.relevant && <button style={{ background: c.warn, border: 'none', borderRadius: 6, padding: '3px 10px' }}>Restore</button>}
                    </div>
                  </td></tr>
                ),
              ])}
            </tbody>
          </table>
        </main>
        <aside style={{ borderLeft: `1px solid ${c.line}`, padding: 14, overflow: 'auto' }}>
          <div style={{ color: c.mute, fontSize: 11, letterSpacing: 1, marginBottom: 8 }}>ACTIVITY</div>
          {lines.map((l, i) => <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 10 }}><span style={{ color: c.ok }}>●</span><span>{l}</span></div>)}
          {running && <div style={{ color: c.mute }}>● working…</div>}
          <input placeholder="Refine… (e.g. only central banks)" style={{ width: '100%', marginTop: 12, padding: 8, background: c.card, color: c.ink, border: `1px solid ${c.line}`, borderRadius: 6 }} />
        </aside>
      </div>

      {showMemory && (
        <div onClick={() => setShowMemory(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', display: 'grid', placeItems: 'center' }}>
          <div onClick={e => e.stopPropagation()} style={{ width: 520, background: c.card, border: `1px solid ${c.line}`, borderRadius: 12, padding: 20 }}>
            <h3 style={{ marginTop: 0 }}>What I remember about you</h3>
            {memory.map(m => <div key={m.fact} style={{ padding: '8px 0', borderTop: `1px solid ${c.line}` }}>{m.fact}<div style={{ color: c.mute, fontSize: 12 }}>{m.source} · {m.evidence} · <a style={{ color: c.accent }} href="#">forget</a></div></div>)}
          </div>
        </div>
      )}
    </div>
  )
}
