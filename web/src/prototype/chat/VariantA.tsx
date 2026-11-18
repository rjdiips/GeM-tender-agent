// PROTOTYPE variant A: chat transcript in the middle, history on the left, results in a tabbed side panel.
import { useState } from 'react'
import { history, memory, request, type Bid } from './data'
import { useReplay } from './useReplay'

export const name = 'Chat + side panel'

const c = { bg: '#f6f7f9', panel: '#fff', line: '#e3e6ea', ink: '#1d2430', mute: '#6b7480', accent: '#2557d6', ok: '#127a3e', warn: '#b4540a' }

export default function VariantA() {
  const { lines, shown, running, replay } = useReplay()
  const [tab, setTab] = useState<'results' | 'rejected' | 'memory'>('results')
  const [pop, setPop] = useState<string | null>(null)
  const relevant = shown.filter(b => b.relevant)
  const rejected = shown.filter(b => !b.relevant)
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '230px 1fr 46%', height: '100vh', font: '14px system-ui', color: c.ink, background: c.bg }}>
      <aside style={{ borderRight: `1px solid ${c.line}`, padding: 14, background: c.panel }}>
        <div style={{ fontWeight: 700, marginBottom: 14 }}>GeM Tender Agent</div>
        <button style={{ width: '100%', padding: 8, marginBottom: 12, border: `1px solid ${c.line}`, borderRadius: 6, background: c.bg }}>+ New search</button>
        {history.map(h => (
          <div key={h.title} style={{ padding: '8px 6px', borderRadius: 6, marginBottom: 4, background: h.when.startsWith('today 13') ? '#e8eefc' : 'transparent' }}>
            <div style={{ fontSize: 13 }}>{h.title}</div>
            <div style={{ fontSize: 12, color: c.mute }}>{h.when} · {h.found} found {h.isNew ? <b style={{ color: c.accent }}>· {h.isNew} new</b> : null}</div>
          </div>
        ))}
      </aside>

      <main style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
          <div style={{ marginLeft: 'auto', maxWidth: 420, background: c.accent, color: '#fff', padding: '10px 14px', borderRadius: '14px 14px 2px 14px' }}>{request}</div>
          <div style={{ marginTop: 18, maxWidth: 560 }}>
            {lines.map((l, i) => (
              <div key={i} style={{ background: c.panel, border: `1px solid ${c.line}`, borderRadius: 10, padding: '8px 12px', marginBottom: 8, fontSize: 13 }}>
                {i === 0 ? <><b>Understood:</b> {l}</> : l}
              </div>
            ))}
            {relevant.map(b => (
              <div key={b.bidNumber} onClick={() => setTab('results')} style={{ background: c.panel, borderLeft: `4px solid ${c.ok}`, borderRadius: 8, padding: '8px 12px', marginBottom: 8, cursor: 'pointer' }}>
                <div style={{ fontSize: 12, color: c.mute }}>{b.bidNumber} · closes {b.closing}</div>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{b.title.slice(0, 90)}</div>
              </div>
            ))}
            {running && <div style={{ color: c.mute, fontSize: 13 }}>working…</div>}
          </div>
        </div>
        <div style={{ padding: 14, borderTop: `1px solid ${c.line}`, background: c.panel, display: 'flex', gap: 8 }}>
          <input placeholder="Ask for tenders, or refine: “only central banks”" style={{ flex: 1, padding: 10, border: `1px solid ${c.line}`, borderRadius: 8 }} />
          <button onClick={replay} style={{ padding: '0 14px', borderRadius: 8, border: 'none', background: c.accent, color: '#fff' }}>Replay run</button>
        </div>
      </main>

      <section style={{ borderLeft: `1px solid ${c.line}`, background: c.panel, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{ display: 'flex', borderBottom: `1px solid ${c.line}` }}>
          {([['results', `Results (${relevant.length})`], ['rejected', `Rejected (${rejected.length})`], ['memory', 'Memory']] as const).map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} style={{ padding: '12px 16px', border: 'none', background: 'none', borderBottom: tab === k ? `2px solid ${c.accent}` : '2px solid transparent', fontWeight: tab === k ? 700 : 400 }}>{label}</button>
          ))}
          <span style={{ flex: 1 }} />
          <button style={{ margin: 8, padding: '4px 10px', border: `1px solid ${c.line}`, borderRadius: 6, background: c.bg }}>⬇ Zip of all</button>
        </div>
        <div style={{ overflow: 'auto', padding: 12 }}>
          {tab === 'memory' ? memory.map(m => (
            <div key={m.fact} style={{ borderBottom: `1px solid ${c.line}`, padding: '10px 4px' }}>
              <div style={{ fontWeight: 600 }}>{m.fact}</div>
              <div style={{ fontSize: 12, color: c.mute }}>{m.source} since {m.since} · {m.evidence}</div>
              <div style={{ marginTop: 4, fontSize: 12 }}><a href="#">edit</a> · <a href="#">forget</a></div>
            </div>
          )) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr style={{ textAlign: 'left', color: c.mute }}><th>Bid</th><th>Buyer</th><th>Closes</th><th>EMD / turnover</th><th>Why</th><th /></tr></thead>
              <tbody>
                {(tab === 'results' ? relevant : rejected).map((b: Bid) => (
                  <tr key={b.bidNumber} style={{ borderTop: `1px solid ${c.line}`, verticalAlign: 'top' }}>
                    <td style={{ padding: '8px 4px' }}><a href="#">{b.bidNumber}</a><div style={{ color: c.mute, fontSize: 12 }}>{b.title.slice(0, 50)}…</div></td>
                    <td style={{ padding: 4, fontSize: 12 }}>{b.buyer.split(' / ')[1]}</td>
                    <td style={{ padding: 4, whiteSpace: 'nowrap' }}>{b.closing}</td>
                    <td style={{ padding: 4, fontSize: 12 }}>{b.emd ?? '—'}<br />{b.turnover ?? ''}</td>
                    <td style={{ padding: 4, position: 'relative' }}>
                      {b.reason}{' '}
                      {b.evidence.length > 0 && <a href="#" onClick={e => { e.preventDefault(); setPop(pop === b.bidNumber ? null : b.bidNumber) }}>evidence</a>}
                      {pop === b.bidNumber && (
                        <div style={{ position: 'absolute', right: 0, top: '100%', width: 340, background: '#fff', border: `1px solid ${c.line}`, boxShadow: '0 6px 20px rgba(0,0,0,.15)', padding: 10, zIndex: 5, borderRadius: 8 }}>
                          {b.evidence.map(e => <div key={e.quote} style={{ marginBottom: 8 }}>“{e.quote}”<div style={{ fontSize: 11, color: c.mute }}>{e.file} p.{e.page} {e.verified ? '✓ found in document' : '⚠ not found'}</div></div>)}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: 4 }}>{tab === 'results' ? <button>⬇</button> : <button style={{ color: c.warn }}>Restore</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  )
}
