import { daysLeft } from '../lib/dates'
import { IconClock } from './icons'

/** Days until closing; amber when 3 days or fewer remain. */
export function Urgency({ closing, now }: { closing: string; now: string }) {
  const d = daysLeft(closing, now)
  const closed = closing <= now  // both IST ISO strings: "closed" from the closing minute, even on the day
  const label = closed ? 'closed' : d === 0 ? 'today' : `${d}d`
  const tip = closed ? 'closed' : d === 0 ? 'closes today' : `closes in ${d} day${d === 1 ? '' : 's'}`
  return <span className={`gt-due ${!closed && d > 3 ? 'calm' : ''}`} title={tip}><IconClock width={12} height={12} />{label}</span>
}
