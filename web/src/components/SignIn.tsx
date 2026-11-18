import { useState } from 'react'
import type { Theme } from '../theme/useTheme'
import './Sidebar.css'
import './Thread.css'

/** Simple username sign-in on the LAN (spec 4.1); the company's auth can replace it. */
export function SignIn({ theme, onSignIn }: { theme: Theme; onSignIn: (name: string) => Promise<void> }) {
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await onSignIn(name.trim())
    } catch {
      setError('Use letters, digits, dots, dashes or underscores.')
    }
  }
  return (
    <div className="gt" data-theme={theme} style={{ display: 'grid', placeItems: 'center', gridTemplateColumns: '1fr' }}>
      <form className="gt-glass" style={{ width: 360 }} onSubmit={submit}>
        <div className="gt-brand"><span className="gt-logo" />Tender Agent</div>
        <label className="gt-k" htmlFor="signin-name">Your name</label>
        <div className="gt-box" style={{ marginTop: 6 }}>
          <input id="signin-name" aria-label="Your name" value={name} onChange={e => setName(e.target.value)} autoFocus placeholder="e.g. asha" />
          <button className="gt-send" type="submit" aria-label="Sign in" disabled={!name.trim()}>→</button>
        </div>
        {error && <div style={{ color: 'var(--bad)', fontSize: 12.5, marginTop: 8 }}>{error}</div>}
      </form>
    </div>
  )
}
