import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { money } from '../lib/money'
import { QueriesBy } from './QueriesBy'
import { Urgency } from './Urgency'

describe('result details', () => {
  it('shows EMD amounts one way', () => {
    expect(money('2650000')).toBe('₹26,50,000')  // a bare number, as the model often gives it
    expect(money('Rs. 27,34,000/-')).toBe('Rs. 27,34,000/-')  // written out: kept
    expect(money('No')).toBe('none')
    expect(money(null)).toBe('—')
  })

  it('calls a bid closed from its closing minute, even on the day', () => {
    const { rerender } = render(<Urgency closing="2026-11-16T12:00:00" now="2026-11-16T13:42:00" />)
    expect(screen.getByText('closed')).toHaveAttribute('title', 'closed')
    rerender(<Urgency closing="2026-11-16T15:00:00" now="2026-11-16T13:42:00" />)
    expect(screen.getByText('today')).toHaveAttribute('title', 'closes today')
    rerender(<Urgency closing="2026-11-17T15:00:00" now="2026-11-16T13:42:00" />)
    expect(screen.getByText('1d')).toHaveAttribute('title', 'closes in 1 day')
  })

  it('shows the last date for queries, where it came from, and marks an unconfirmed one', () => {
    const q = { when: '2026-11-20T17:00:00', source: 'documents' as const, file: 'rfp.pdf', file_name: 'RFP', page: 1,
                quote: 'Last date for submission of Queries : 20th November 2026, 05.00 PM', verified: true }
    const { container, rerender } = render(<QueriesBy queries={q} now="2026-11-17T10:00:00" />)
    const box = container.firstElementChild!
    expect(screen.getByText('20 Nov 17:00')).toBeInTheDocument()
    expect(screen.getByText('queries')).toBeInTheDocument()
    expect(box.getAttribute('title')).toContain('RFP p.1')
    expect(box.getAttribute('title')).toContain('3 days left')
    rerender(<QueriesBy queries={{ ...q, verified: false }} now="2026-11-19T10:00:00" />)
    expect(container.firstElementChild).toHaveClass('soon')
    expect(screen.getByText('unconfirmed')).toBeInTheDocument()
    rerender(<QueriesBy queries={q} now="2026-11-20T10:00:00" />)
    expect(screen.getByText('queries today')).toBeInTheDocument()
    rerender(<QueriesBy queries={q} now="2026-11-21T10:00:00" />)
    expect(screen.getByText('queries closed')).toBeInTheDocument()
    expect(container.firstElementChild).toHaveClass('passed')
    rerender(<QueriesBy queries={{ ...q, source: 'gem_pre_bid' }} now="2026-11-17T10:00:00" />)
    expect(screen.getByText('pre-bid')).toBeInTheDocument()
    expect(container.firstElementChild!.getAttribute('title')).toContain('Pre-bid meeting')
  })
})
