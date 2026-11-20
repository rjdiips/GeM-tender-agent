// PROTOTYPE: floating variant switcher (dev builds only).
import { useEffect } from 'react'

export function PrototypeSwitcher({ variants, current, names, onChange }: {
  variants: string[]; current: string; names: Record<string, string>; onChange: (v: string) => void
}) {
  const i = variants.indexOf(current)
  const go = (d: number) => onChange(variants[(i + d + variants.length) % variants.length])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      if (e.key === 'ArrowLeft') go(-1)
      if (e.key === 'ArrowRight') go(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  if (import.meta.env.PROD) return null
  const btn: React.CSSProperties = { background: 'none', border: 'none', color: '#fff', fontSize: 18, cursor: 'pointer', padding: '0 10px' }
  return (
    <div style={{ position: 'fixed', bottom: 16, right: 16, background: '#e11d48', color: '#fff',
      borderRadius: 999, padding: '6px 10px', display: 'flex', alignItems: 'center', boxShadow: '0 4px 16px rgba(0,0,0,.35)',
      font: '600 13px system-ui', zIndex: 9999 }}>
      <button style={btn} onClick={() => go(-1)}>←</button>
      <span>PROTOTYPE {current} ({names[current]})</span>
      <button style={btn} onClick={() => go(1)}>→</button>
    </div>
  )
}
