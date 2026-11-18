import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { bid, FakeApi, NOW, run } from './test/fakeApi'

function setup(api = new FakeApi(), pollMs?: number) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })
  render(<App api={api} now={() => NOW} initialRunId="r1" pollMs={pollMs} />)
  return { api, user: userEvent.setup() }
}

describe('web chat', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('lists past runs and saved searches with their new counts', async () => {
    setup()
    const side = await screen.findByRole('navigation', { name: 'Runs' })
    expect(within(side).getByText('AML software for banks')).toBeInTheDocument()
    expect(within(side).getByText('+2')).toBeInTheDocument()
  })

  it('shows what was understood and the timestamped progress', async () => {
    setup()
    expect(await screen.findByText('Here’s what I’m looking for')).toBeInTheDocument()
    expect(screen.getByText('Looking for consultants advising banks, published 2–16 Nov.')).toBeInTheDocument()
    expect(screen.getByText('Management consultancy · banks')).toBeInTheDocument()
    expect(screen.getByText('Published 2–16 Nov')).toBeInTheDocument()
    expect(screen.getByText('36,104 open bids')).toBeInTheDocument()
    expect(screen.getByText('9 likely from titles')).toBeInTheDocument()
    expect(screen.getByText('not: engineering consultancy')).toBeInTheDocument()
    expect(screen.getByText('13:40:09')).toBeInTheDocument()
  })

  it('splits relevant and quarantined bids with counts and urgency', async () => {
    setup()
    const tabs = await screen.findByRole('tablist')
    expect(within(tabs).getByRole('tab', { name: /Relevant 1/ })).toHaveAttribute('aria-selected', 'true')
    expect(within(tabs).getByRole('tab', { name: /Quarantine 1/ })).toBeInTheDocument()
    const panel = screen.getByRole('tabpanel')
    expect(within(panel).getByText('Hiring of Consultants - Management Consultants')).toBeInTheDocument()
    expect(within(panel).getByText('7d')).toBeInTheDocument()
    expect(within(panel).getByText('Union Bank Of India')).toHaveAttribute('title', 'Ministry of Finance / Department of Financial Services')
    expect(within(panel).getByText('Rs 2,10,00,000')).toBeInTheDocument()
  })

  it('expands evidence inline with verified quotes and file downloads', async () => {
    const { user } = setup()
    await user.click(await screen.findByRole('button', { name: /1 quote · from the documents · 2 files/ }))  // quotes and files, always
    const ev = screen.getByRole('region', { name: 'Quotes and files for GEM/2026/B/8162911' })
    expect(within(ev).getByText(/The scope of Project Muskaan/)).toBeInTheDocument()
    expect(within(ev).getByText('verified')).toBeInTheDocument()
    const rfp = within(ev).getByRole('link', { name: /RFP for Project Muskaan/ })
    expect(rfp).toHaveAttribute('href', '/api/files/1')
    expect(rfp).toHaveAttribute('download', 'GEM-2026-B-8162911 - RFP for Project Muskaan.pdf')
    expect(rfp).toHaveTextContent('×2')  // the same document uploaded twice, shown once
    expect(rfp.getAttribute('title')).toContain('also uploaded as: Scope of Work, MUSKAN2.pdf')
    expect(rfp.getAttribute('title')).toContain('MUSKANrfp.pdf')
    const src = within(ev).getByText('RFP for Project Muskaan', { selector: '.gt-src-name' }).closest('.gt-src')!
    expect(src).toHaveTextContent('RFP for Project Muskaan· p.30')  // the quote names its document and page
    expect(src).toHaveAttribute('title', 'RFP for Project Muskaan, page 30')  // full name on hover when cut
    expect(within(ev).queryByRole('link', { name: /1791988256/ })).not.toBeInTheDocument()
    expect(within(ev).getByText(/placeholder/)).toBeInTheDocument()
  })

  it('says what the quotes prove instead of a confidence percentage', async () => {
    const api = new FakeApi()
    api.current = run({ results: [bid(), bid({ b_id: 3, bid_number: 'GEM/2026/B/3', proof: 'title' }),
                                  bid({ b_id: 4, bid_number: 'GEM/2026/B/4', proof: 'none' })] })
    setup(api)
    expect(await screen.findByRole('button', { name: /1 quote · from the documents/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /1 quote · title only/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /1 quote · none found in the documents/ })).toBeInTheDocument()
    expect(screen.queryByText(/% sure/)).not.toBeInTheDocument()
  })

  it('says why a quote is not counted: too short, or not found in the documents', async () => {
    const api = new FakeApi()
    api.current = run({ results: [bid({ evidence: [
      { quote: 'EMD Amount 50000', file: 'bid.pdf', file_name: 'Bid document', page: 2, verified: false, check: 'short' },
      { quote: 'the bank needs an anti money laundering system', file: 'rfp.pdf', file_name: 'RFP', page: 4, verified: false, check: 'not_found' },
    ] })] })
    const { user } = setup(api)
    await user.click(await screen.findByRole('button', { name: /2 quotes/ }))
    const ev = screen.getByRole('region', { name: /Quotes and files/ })
    expect(within(ev).getByText('too short to count')).toBeInTheDocument()
    expect(within(ev).getByText('not found in documents')).toBeInTheDocument()
  })

  it('opens the files of a bid ruled out without quotes', async () => {
    // triage decides from the title and first pages: no quotes, but the files were fetched (16 Nov)
    const { user } = setup()
    await user.click(await screen.findByRole('tab', { name: /Quarantine/ }))
    await user.click(screen.getByRole('button', { name: /decided from the title and first pages · 2 files/ }))
    const ev = screen.getByRole('region', { name: 'Quotes and files for GEM/2026/B/8264466' })
    expect(within(ev).getByRole('link', { name: /RFP for Project Muskaan/ })).toHaveAttribute('href', '/api/files/1')
  })

  it('opens a tender by clicking anywhere on its card, but not from its links or the open box', async () => {
    const { user } = setup()
    await user.click(await screen.findByText('Hires management consultants for a bank.'))
    const box = screen.getByRole('region', { name: 'Quotes and files for GEM/2026/B/8162911' })
    await user.click(within(box).getByText(/The scope of Project Muskaan/))
    expect(box).toBeInTheDocument()  // reading a quote does not close it
    await user.click(screen.getByText('Hires management consultants for a bank.'))
    expect(screen.queryByRole('region', { name: /Quotes and files/ })).not.toBeInTheDocument()
  })

  it('marks a bid whose documents could not be fetched as not read', async () => {
    const api = new FakeApi()
    api.current = run({ results: [bid({ relevant: false, files: [], evidence: [], error: '403',
                                        reason: 'Not read: GeM refused the request (403) when its documents were fetched.' })] })
    const { user } = setup(api)
    await user.click(await screen.findByRole('tab', { name: /Quarantine/ }))
    expect(screen.getByText('not read')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /file/ })).not.toBeInTheDocument()  // nothing to open
  })

  it('names files that could not be read in words', async () => {
    const { user } = setup()
    await user.click(await screen.findByText('Hires management consultants for a bank.'))
    expect(screen.getByText('placeholder, no content')).toBeInTheDocument()
  })

  it('restores a quarantined bid in one click', async () => {
    const { api, user } = setup()
    await user.click(await screen.findByRole('tab', { name: /Quarantine/ }))
    await user.click(screen.getByRole('button', { name: 'Restore GEM/2026/B/8264466' }))
    expect(api.calls).toContain('restore r1 2')
    expect(screen.getByRole('tab', { name: /Relevant 2/ })).toBeInTheDocument()
  })

  it('shows memory with its source and forgets a fact', async () => {
    const { api, user } = setup()
    await user.click(await screen.findByRole('tab', { name: /Memory/ }))
    expect(await screen.findByText('We sell AML and LOS software to banks')).toBeInTheDocument()
    expect(screen.getByText('stated')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Forget: LOS = Loan Origination System' }))
    expect(api.calls).toContain('forget f2')
    expect(screen.queryByText('LOS = Loan Origination System')).not.toBeInTheDocument()
  })

  it('streams a new request into the thread and the results panel', async () => {
    const api = new FakeApi()
    const fresh = run({ run_id: 'r2', title: 'LOS tenders', request: 'LOS tenders', status: 'running', understood: null, progress: [], results: [] })
    api.script = [
      { type: 'started', run: fresh },
      { type: 'understood', understood: { name: 'Loan Origination System', meaning: 'LOS software', window: 'published 2–16 Nov', summary: 'Looking for LOS software, published 2–16 Nov.', buyers: [], window_count: 36104, likely: 4, not_this: ['Line of Sight'] } },
      { type: 'progress', event: { at: '2026-11-16T13:50:01', text: 'Shortlist: 4 bids.' } },
      { type: 'result', bid: bid({ b_id: 9, bid_number: 'GEM/2026/B/9', title: 'LOS for RRBs' }) },
      { type: 'done', status: 'complete' },
    ]
    const { user } = setup(api)
    await screen.findByText('Management consultancy · banks')
    await user.type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'LOS tenders{Enter}')
    expect(api.calls).toContain('ask LOS tenders (r1)')
    expect(await screen.findByText('Loan Origination System')).toBeInTheDocument()
    expect(screen.getByText('Shortlist: 4 bids.')).toBeInTheDocument()
    expect(within(screen.getByRole('tabpanel')).getByText('LOS for RRBs')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Ask for tenders' })).toHaveValue('')
    const entry = within(screen.getByRole('navigation', { name: 'Runs' })).getByRole('button', { name: /^LOS tenders/ })
    expect(entry).toHaveTextContent('1 found')
  })

  it('shows the running count as one line, the newest', async () => {
    const api = new FakeApi()
    api.script = [
      { type: 'started', run: run({ run_id: 'r3', title: 'LOS tenders', request: 'LOS tenders', status: 'running', understood: null, progress: [], results: [] }) },
      { type: 'progress', event: { at: '2026-11-16T13:50:01', text: 'Shortlist: 4 bids.' } },
      { type: 'progress', event: { at: '2026-11-16T13:50:20', text: 'Read 2 of 4 bids: 5 files downloaded…', kind: 'count' } },
      { type: 'progress', event: { at: '2026-11-16T13:50:40', text: 'Read 3 of 4 bids: 8 files downloaded…', kind: 'count' } },
    ]
    const { user } = setup(api)
    await screen.findByText('Management consultancy · banks')
    await user.type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'LOS tenders{Enter}')
    expect(await screen.findByText('Read 3 of 4 bids: 8 files downloaded…')).toBeInTheDocument()
    expect(screen.queryByText('Read 2 of 4 bids: 5 files downloaded…')).not.toBeInTheDocument()
    expect(screen.getByText('Shortlist: 4 bids.')).toBeInTheDocument()
  })

  it('keeps the last running count above the Done line', async () => {
    const api = new FakeApi()
    api.script = [
      { type: 'started', run: run({ run_id: 'r4', title: 'LOS tenders', request: 'LOS tenders', status: 'running', understood: null, progress: [], results: [] }) },
      { type: 'progress', event: { at: '2026-11-16T13:50:10', text: 'Read 3 of 4 bids: 8 files downloaded…', kind: 'count' } },
      { type: 'progress', event: { at: '2026-11-16T13:50:20', text: 'Read 4 of 4 bids: 10 files downloaded…', kind: 'count' } },
      { type: 'progress', event: { at: '2026-11-16T13:51:00', text: 'Done: 1 relevant, 3 quarantined.' } },
      { type: 'done', status: 'complete' },
    ]
    const { user } = setup(api)
    await screen.findByText('Management consultancy · banks')
    await user.type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'LOS tenders{Enter}')
    expect(await screen.findByText('Done: 1 relevant, 3 quarantined.')).toBeInTheDocument()
    const log = screen.getByRole('list', { name: 'Progress' })
    expect(within(log).getAllByRole('listitem').map(li => li.textContent)).toEqual([
      '13:50:20Read 4 of 4 bids: 10 files downloaded…', '13:51:00Done: 1 relevant, 3 quarantined.'])
  })

  it('shows the request at once and a waiting turn until the server answers, then placeholder cards', async () => {
    const api = new FakeApi()
    api.script = []  // the model is still reading the request: nothing has come back yet
    const { user } = setup(api)
    await screen.findByText('Management consultancy · banks')
    await user.click(screen.getByRole('button', { name: 'New search' }))
    await user.type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'LOS tenders{Enter}')
    expect(screen.getByText('LOS tenders', { selector: '.gt-me-msg' })).toBeInTheDocument()
    expect(screen.getByText('Reading your request…')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Searching' })).toBeInTheDocument()
    expect(screen.queryByText('No relevant tenders yet.')).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'more')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()  // one request at a time
    const fresh = run({ run_id: 'r2', title: 'LOS tenders', request: 'LOS tenders', status: 'running', understood: null, progress: [], results: [] })
    act(() => api.live?.({ type: 'started', run: fresh }))
    expect(screen.queryByText('Reading your request…')).not.toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Searching' })).toBeInTheDocument()  // running, nothing judged yet
    act(() => api.live?.({ type: 'done', status: 'complete' }))
    expect(screen.getByText('No relevant tenders yet.')).toBeInTheDocument()
  })

  it('switches between dark and light from the header', async () => {
    const { user } = setup()
    const app = await screen.findByTestId('app')
    expect(app).toHaveAttribute('data-theme', 'dark')
    await user.click(screen.getByRole('button', { name: 'Switch to light mode' }))
    expect(app).toHaveAttribute('data-theme', 'light')
  })

  it('offers the zip of everything', async () => {
    setup()
    expect(await screen.findByRole('link', { name: /zip/ })).toHaveAttribute('href', '/api/runs/r1/zip')
  })
})


describe('answers and sign-in', () => {
  it('a show event switches tab and a message appears in the thread', async () => {
    const api = new FakeApi()
    api.script = [{ type: 'show', tab: 'memory' }, { type: 'message', text: 'Here is what I remember about you.' }]
    const { user } = setup(api)
    await screen.findByText('Management consultancy · banks')
    await user.type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'what do you remember?{Enter}')
    expect(screen.getByRole('tab', { name: /Memory/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Here is what I remember about you.')).toBeInTheDocument()
    expect(screen.getByText('what do you remember?')).toBeInTheDocument()
  })

  it('asks for a name when not signed in, then loads', async () => {
    const api = new FakeApi()
    api.signedIn = false
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })
    render(<App api={api} now={() => NOW} />)
    const user = userEvent.setup()
    await user.type(await screen.findByRole('textbox', { name: 'Your name' }), 'asha{Enter}')
    expect(api.calls).toContain('login asha')
    expect(await screen.findByRole('navigation', { name: 'Runs' })).toBeInTheDocument()
  })

  it('labels the meaning when a run searched two', async () => {
    const api = new FakeApi()
    api.current = run({ results: [bid({ group: 'Loan Origination System' }), bid({ b_id: 7, bid_number: 'GEM/2026/B/7', group: 'Line of Sight' })] })
    setup(api)
    const panel = await screen.findByRole('tabpanel')
    expect(await within(panel).findByText('Line of Sight')).toBeInTheDocument()
  })
})


describe('stopping a run', () => {
  it('offers Stop only while running, and stops the run when clicked', async () => {
    const api = new FakeApi()
    api.script = [{ type: 'started', run: run({ run_id: 'r5', status: 'running', results: [], progress: [], understood: null }) }]
    api.current = run({ run_id: 'r5', status: 'running', results: [], progress: [], understood: null })
    const { user } = setup(api, 10_000)
    const box = await screen.findByRole('textbox', { name: 'Ask for tenders' })
    await user.type(box, 'LOS tenders{Enter}')
    const stop = await screen.findByRole('button', { name: 'Stop run' })
    expect(screen.getByText('running')).toBeInTheDocument()
    // the send button itself turns into Stop: it sits in the composer, and there is no separate Send meanwhile
    expect(stop.closest('form')).toContainElement(screen.getByRole('textbox', { name: 'Ask for tenders' }))
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: /Stop/ })).not.toBeInTheDocument()
    await user.click(stop)
    expect(api.calls).toContain('stop r5')
    expect(await screen.findByText('stopped')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop run' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
  })

  it('has no Stop button on a finished run', async () => {
    setup()
    await screen.findByText('Management consultancy · banks')
    expect(screen.queryByRole('button', { name: 'Stop run' })).not.toBeInTheDocument()
  })
})

describe('catching up when the stream drops', () => {
  it('polls the run while it is running and shows results the stream never delivered', async () => {
    // A dropped SSE connection (proxy hiccup, laptop asleep) must not leave the panel stale until a refresh.
    const api = new FakeApi()
    const running = run({ run_id: 'r9', status: 'running', results: [], progress: [], understood: null })
    api.script = [{ type: 'started', run: running }]
    api.current = run({ run_id: 'r9', status: 'complete', results: [bid({ bid_number: 'GEM/2026/B/77', title: 'LOS for RRBs' })] })
    setup(api, 20)
    await screen.findByText('Management consultancy · banks')
    await userEvent.setup().type(screen.getByRole('textbox', { name: 'Ask for tenders' }), 'LOS tenders{Enter}')
    const panel = screen.getByRole('tabpanel')
    expect(await within(panel).findByText('LOS for RRBs')).toBeInTheDocument()
    expect(screen.getByText('complete')).toBeInTheDocument()
  })

  it('stops polling once the run is complete', async () => {
    const api = new FakeApi()
    setup(api, 20)
    await screen.findByText('Management consultancy · banks')
    const before = api.calls.filter(c => c.startsWith('getRun')).length
    await new Promise(r => setTimeout(r, 80))
    expect(api.calls.filter(c => c.startsWith('getRun')).length).toBe(before)
  })
})
