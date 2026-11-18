import type { QueriesDeadline } from '../api/types'
import { daysLeft, shortDateTime } from '../lib/dates'

/** The last date for queries, under the closing date and laid out like it (a label, then the date), so it fits the
 * column: amber when 2 days or fewer remain, struck once passed. The hover says where the date came from; one whose
 * words were not found in the documents says "unconfirmed". */
export function QueriesBy({ queries, now }: { queries: QueriesDeadline; now: string }) {
  const passed = queries.when <= now  // both IST ISO strings
  const d = daysLeft(queries.when, now)
  const where = queries.source === 'gem_pre_bid'
    ? `GeM's pre-bid date, ${queries.file_name || queries.file} p.${queries.page}`
    : queries.verified
      ? `${queries.file_name || queries.file} p.${queries.page}: “${queries.quote}”`
      : `the AI's reading; these words were not found in the documents, so check it: “${queries.quote}”`
  const due = passed ? 'passed' : d === 0 ? 'today' : `${d} day${d === 1 ? '' : 's'} left`
  // GeM's pre-bid date is the meeting, not always the last date for queries: name which one this is
  const what = queries.source === 'gem_pre_bid' || queries.kind === 'pre_bid_meeting' ? 'pre-bid' : 'queries'
  const label = passed ? `${what} ${what === 'queries' ? 'closed' : 'passed'}` : d === 0 ? `${what} today` : what
  return (
    <div className={`gt-qby ${passed ? 'passed' : d <= 2 ? 'soon' : ''}`} title={`${what === 'pre-bid' ? 'Pre-bid meeting' : 'Last date for queries'} (${due}). From ${where}`}>
      <div className="gt-qby-k">{label}</div>
      <div className="gt-mono gt-qby-when">{shortDateTime(queries.when)}</div>
      {!queries.verified && <div className="gt-qby-k">unconfirmed</div>}
    </div>
  )
}
