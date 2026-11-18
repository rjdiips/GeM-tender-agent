// Dates arrive as ISO strings in IST clock time; they are shown as such, never shifted by the browser's zone.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function parts(iso: string) {
  const [d, t = '00:00'] = iso.split('T')
  const [y, m, day] = d.split('-').map(Number)
  const [hh, mm] = t.split(':')
  return { y, m, day, hh, mm }
}

/** "18 Nov 15:00" */
export function shortDateTime(iso: string): string {
  const p = parts(iso)
  return `${p.day} ${MONTHS[p.m - 1]} ${p.hh}:${p.mm}`
}

/** "13:40:09" */
export function clockTime(iso: string): string {
  return iso.split('T')[1]?.slice(0, 8) ?? ''
}

/** Whole days from `now` to `closing` (calendar days, both IST). */
export function daysLeft(closing: string, now: string): number {
  const a = parts(now)
  const b = parts(closing)
  return Math.round((Date.UTC(b.y, b.m - 1, b.day) - Date.UTC(a.y, a.m - 1, a.day)) / 86_400_000)
}

/** "today 13:40", "yesterday", "14 Nov" */
export function relativeDay(iso: string, now: string): string {
  const d = -daysLeft(iso, now)
  if (d === 0) return `today ${parts(iso).hh}:${parts(iso).mm}`
  if (d === 1) return 'yesterday'
  const p = parts(iso)
  return `${p.day} ${MONTHS[p.m - 1]}`
}
