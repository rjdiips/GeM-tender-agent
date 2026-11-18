import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { BidResult } from '../api/types'
import { bid, NOW, run } from '../test/fakeApi'
import { ResultsPanel } from './ResultsPanel'
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

describe('borderline section', () => {
  const border = () => bid({ b_id: 5, bid_number: 'GEM/2026/B/5555', state: 'borderline', relevant: false,
    question: 'Does AI-enabled analytics count as AI software?', reason: 'Analytics with some ML.' })
  const panel = (results: BidResult[], over: Partial<React.ComponentProps<typeof ResultsPanel>> = {}) => (
    <ResultsPanel run={run({ results })} tab="relevant" onTab={() => {}} now={NOW} memory={[]}
                  onRestore={() => {}} onRule={() => {}} onUnrule={() => {}} onForget={() => {}} onEdit={() => {}} {...over} />)

  it('borderline bids sit above the relevant ones with their question and two buttons', () => {
    render(panel([bid(), border()]))
    const heading = screen.getByRole('heading', { name: 'Borderline: your call' })
    expect(screen.getByText('Does AI-enabled analytics count as AI software?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Counts for me' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Doesn't count" })).toBeInTheDocument()
    const tab = screen.getByRole('tab', { name: /Relevant 1/ })
    expect(tab).toHaveTextContent('1 to decide')
    expect(heading.compareDocumentPosition(screen.getByText('GEM/2026/B/8162911')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows no Borderline section when nothing is borderline', () => {
    render(panel([bid()]))
    expect(screen.queryByRole('heading', { name: /Borderline/ })).not.toBeInTheDocument()
  })

  it('clicking a ruling button calls onRule', async () => {
    const onRule = vi.fn()
    render(panel([border()], { onRule }))
    await userEvent.click(screen.getByRole('button', { name: 'Counts for me' }))
    expect(onRule).toHaveBeenCalledWith(expect.objectContaining({ b_id: 5 }), 'counts')
    await userEvent.click(screen.getByRole('button', { name: "Doesn't count" }))
    expect(onRule).toHaveBeenLastCalledWith(expect.objectContaining({ b_id: 5 }), 'not')
  })

  it('a bid decided by an earlier ruling shows the ruling and an undo link', async () => {
    const onUnrule = vi.fn()
    const ruled = bid({ b_id: 6, ruling: { decision: 'counts', by: 'earlier', fact: 'AI that is analytics' } })
    render(panel([ruled], { onUnrule }))
    expect(screen.getByText('decided by your earlier ruling: "AI that is analytics"')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'undo' }))
    expect(onUnrule).toHaveBeenCalledWith(expect.objectContaining({ b_id: 6 }))
  })

  it('the zip link is hidden when no bid is relevant, even with borderline bids', () => {
    render(panel([border()]))
    expect(screen.queryByText('.zip')).not.toBeInTheDocument()
  })

  it('memory lists a ruling fact with its topic', () => {
    render(panel([], { tab: 'memory', memory: [{ id: 'f9', fact: 'AI that is analytics counts', evidence: 'you ruled on 18 Nov', source: 'stated',
      since: '2026-11-18', kind: 'ruling', topic: 'AI software' }] }))
    expect(screen.getByText('AI that is analytics counts')).toBeInTheDocument()
    expect(screen.getByText('ruling · AI software')).toBeInTheDocument()
  })
})
