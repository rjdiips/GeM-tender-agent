import { describe, expect, it } from 'vitest'
import { clockTime, daysLeft, relativeDay, shortDateTime } from './dates'

const NOW = '2026-11-16T13:42:00'

describe('dates are shown in IST clock time as sent', () => {
  it('formats a closing date', () => expect(shortDateTime('2026-11-18T15:00:00')).toBe('18 Nov 15:00'))
  it('formats a log time', () => expect(clockTime('2026-11-16T13:40:09')).toBe('13:40:09'))
  it('counts calendar days left', () => {
    expect(daysLeft('2026-11-18T15:00:00', NOW)).toBe(2)
    expect(daysLeft('2026-11-16T23:59:00', NOW)).toBe(0)
    expect(daysLeft('2026-12-07T17:00:00', NOW)).toBe(21)
  })
  it('describes when a run happened', () => {
    expect(relativeDay('2026-11-16T10:05:00', NOW)).toBe('today 10:05')
    expect(relativeDay('2026-11-15T09:00:00', NOW)).toBe('yesterday')
    expect(relativeDay('2026-11-10T09:00:00', NOW)).toBe('10 Nov')
  })
})
