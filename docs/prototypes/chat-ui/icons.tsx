// PROTOTYPE: a small inline icon set (stroke icons, 1.75px, currentColor) shared by the variants.
import type { SVGProps } from 'react'

const base = (d: React.ReactNode) => (p: SVGProps<SVGSVGElement>) => (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
    strokeLinecap="round" strokeLinejoin="round" {...p}>{d}</svg>
)

export const IconPlus = base(<><path d="M12 5v14" /><path d="M5 12h14" /></>)
export const IconSend = base(<><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>)
export const IconSearch = base(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>)
export const IconDownload = base(<><path d="M12 4v11" /><path d="m7 10 5 5 5-5" /><path d="M5 20h14" /></>)
export const IconCheck = base(<path d="m5 12 5 5 9-10" />)
export const IconClock = base(<><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>)
export const IconBuilding = base(<><rect x="5" y="3" width="14" height="18" rx="1.5" /><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1" /></>)
export const IconFile = base(<><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>)
export const IconQuote = base(<><path d="M7 7h4v4c0 3-2 5-4 6" /><path d="M14 7h4v4c0 3-2 5-4 6" /></>)
export const IconUndo = base(<><path d="M9 14 4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12h-3" /></>)
export const IconBrain = base(<><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a2 2 0 0 0-3-1z" /><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1" /></>)
export const IconHistory = base(<><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" /></>)
export const IconSparkle = base(<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />)
export const IconX = base(<><path d="M6 6l12 12" /><path d="M18 6 6 18" /></>)
export const IconArchive = base(<><rect x="3" y="4" width="18" height="5" rx="1" /><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9" /><path d="M10 13h4" /></>)
export const IconShield = base(<path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z" />)
export const IconPlay = base(<path d="M7 5v14l11-7z" />)
export const IconExternal = base(<><path d="M14 4h6v6" /><path d="M20 4 11 13" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>)
export const IconSun = base(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>)
export const IconMoon = base(<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />)

/** Days until a sample closing date like "18 Nov 15:00", from the sample "today" (16 Nov). */
export function daysLeft(closing: string): number {
  const [d, mon] = closing.split(' ')
  const day = Number(d) + (mon === 'Dec' ? 30 : 0)
  return day - 16
}
