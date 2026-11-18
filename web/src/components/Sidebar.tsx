import type { RunListItem, UserView } from '../api/types'
import { relativeDay } from '../lib/dates'
import { IconHistory, IconPlus } from './icons'
import './Sidebar.css'

function initials(name: string) {
  return name.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase()
}

export function Sidebar({ runs, currentId, user, memoryCount, now, onOpen, onNew }: {
  runs: RunListItem[]; currentId: string | null; user: UserView | null; memoryCount: number; now: string
  onOpen: (runId: string) => void; onNew: () => void
}) {
  return (
    <aside className="gt-side">
      <div className="gt-brand"><span className="gt-logo" />Tender Agent <span className="gt-mono" style={{ color: 'var(--mute)', marginLeft: 'auto' }}>GeM</span></div>
      <button className="gt-new" onClick={onNew}><IconPlus />New search</button>
      <nav aria-label="Runs" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
        <div className="gt-label">Runs</div>
        <ul className="gt-runs">
          {runs.map(r => (
            <li key={r.run_id}>
              <button className="gt-run" aria-current={r.run_id === currentId} onClick={() => onOpen(r.run_id)}>
                <div>{r.title}{r.new_count > 0 && <span className="gt-pill">+{r.new_count}</span>}</div>
                <div className="m">
                  {r.saved_search ? <><IconHistory width={11} height={11} /> daily · </> : `${relativeDay(r.when, now)} · `}{r.found} found
                </div>
              </button>
            </li>
          ))}
        </ul>
      </nav>
      {user && (
        <div className="gt-me">
          <span className="gt-av">{initials(user.name)}</span>
          <div><div>{user.name}</div><div className="gt-k">{[user.org, `${memoryCount} memories`].filter(Boolean).join(' · ')}</div></div>
        </div>
      )}
    </aside>
  )
}
