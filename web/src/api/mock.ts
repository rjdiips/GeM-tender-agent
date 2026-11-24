// Demo TenderApi with sample data, used until the backend's chat API exists (build step 6).
// Bid numbers, titles, buyers and dates are real; GEM/2026/B/8162911's verdict is the
// real Judge output; the other verdicts are illustrative.
import type { BidResult, MemoryFactView, RunEvent, RunListItem, RunStatusSummary, RunView, TenderApi } from './types'

const gem = (bId: number) => `https://bidplus.gem.gov.in/showbidDocument/${bId}`
// [shown name, pages, status, file name on GeM, identical copies]
const files = (...f: [string, number, BidResult['files'][number]['status'], string, number?][]) =>
  f.map(([name, pages, status, filename, copies = 1], i) => ({
    name, title: null, also: [], copies, filename, pages, status, download_name: `${name}.pdf`, url: `#demo-file-${i}` }))

const RESULTS: BidResult[] = [
  { b_id: 10133210, bid_number: 'GEM/2026/B/8263340', title: 'Hiring of Consultants - Milestone/Deliverable Based - Management Consultants, Functional Consultant',
    buyer: 'Ministry of Finance / Department of Financial Services', buyer_org: 'NaBFID', published: '2026-11-11T14:22:45', closing: '2026-11-18T15:00:00',
    relevant: true, state: 'relevant', question: '', ruling: null, restored: false, confidence: 0.86, read_because: 'match', reason: 'A public sector bank is hiring functional consultants to redesign retail lending processes.',
    evidence: [{ quote: 'The consultant shall assist the Bank in re-engineering its retail credit processes and branch operating model', file: 'RFP_Consultancy.pdf', file_name: 'RFP for Functional Consultants (Retail Lending)', page: 12, verified: true }],
    key_facts: { emd: 'Rs 10,00,000', min_turnover: 'Rs 50 crore', contract_period: '18 months', estimated_value: null, scope_summary: null },
    files: files(['Bid document', 6, 'ok', 'GeM-Bidding-10133210.pdf'], ['RFP for Functional Consultants (Retail Lending)', 64, 'ok', 'RFP_Consultancy.pdf']), needs_review: false, gem_url: gem(10133210), group: 'Management consultancy', error: '' },
  { b_id: 10037444, bid_number: 'GEM/2026/B/8162911', title: 'Hiring of Consultants - Milestone/Deliverable Based - Management Consultants, AS PER RFP; Business Transformation Expert',
    buyer: 'Ministry of Finance / Department of Financial Services', buyer_org: 'Bank of Maharashtra', published: '2026-10-14T11:02:00', closing: '2026-11-23T16:00:00',
    relevant: true, state: 'relevant', question: '', ruling: null, restored: false, confidence: 1, read_because: 'net', reason: 'The tender hires management consultants for organisational and business transformation within a public sector bank.',
    evidence: [
      { quote: 'The scope of Project Muskaan shall encompass Retail Operations and Branch Banking across both asset and liability products, including centralization of activities', file: 'MUSKANrfp.pdf', file_name: 'RFP for Project Muskaan', page: 30, verified: true },
      { quote: 'Bidder should have experience of minimum 5 years in providing the Services in field of consultancy in organizational transformation', file: 'MUSKANrfp.pdf', file_name: 'RFP for Project Muskaan', page: 37, verified: true }],
    key_facts: { emd: 'Rs 2,10,00,000', min_turnover: 'Rs 200 crore', contract_period: '2 years', estimated_value: null, scope_summary: null },
    files: files(['Bid document', 7, 'ok', 'GeM-Bidding-10037444.pdf'], ['RFP for Project Muskaan', 112, 'ok', 'MUSKANrfp.pdf', 3], ['Scope of Work', 1, 'placeholder', '1791988256.pdf'], ['Service level agreement (GeM)', 1, 'ok', '102149993597184']),
    needs_review: false, gem_url: gem(10037444), group: 'Management consultancy', error: '' },
  { b_id: 10164020, bid_number: 'GEM/2026/B/8264466', title: 'Financial Audit Services - Concurrent Audit of Digital Banking Operations; CA Firm',
    buyer: 'Ministry of Finance / Department of Financial Services', buyer_org: 'Bank of Baroda', published: '2026-11-16T12:01:10', closing: '2026-12-07T17:00:00',
    relevant: false, state: 'quarantined', question: '', ruling: null, restored: false, confidence: 0.8, read_because: 'match', reason: 'Concurrent audit by a CA firm: audit services are related but not management consultancy.',
    evidence: [{ quote: 'Appointment of Chartered Accountant firms for concurrent audit of digital banking operations', file: 'Audit_Scope.pdf', file_name: 'Scope of Work', page: 2, verified: true }],
    key_facts: null, files: files(['Bid document', 5, 'ok', 'GeM-Bidding-10164020.pdf'], ['Scope of Work', 14, 'ok', 'Audit_Scope.pdf']), needs_review: false, gem_url: gem(10164020), group: 'Management consultancy', error: '' },
  { b_id: 10170330, bid_number: 'GEM/2026/B/8269932', title: 'Cyber Security Audit - CSCRF', buyer: 'Ministry of Finance / Department of Financial Services', buyer_org: 'Bank of Baroda',
    published: '2026-11-12T22:30:31', closing: '2026-11-19T09:00:00', relevant: false, state: 'borderline', read_because: 'unsure', question: 'Does a cyber security audit count as consultancy for you?', ruling: null, restored: false, confidence: 0.85,
    reason: 'A cyber security audit under SEBI CSCRF, not a consultancy engagement.',
    evidence: [{ quote: 'Conduct of cyber security audit in line with the SEBI Cybersecurity and Cyber Resilience Framework', file: 'GeM-Bidding-10170330.pdf', file_name: 'Bid document', page: 3, verified: true }],
    key_facts: null, files: files(['Bid document', 5, 'ok', 'GeM-Bidding-10170330.pdf']), needs_review: false, gem_url: gem(10170330), group: 'Management consultancy', error: '' },
]

const RUN: RunView = {
  run_id: 'demo-1', title: 'Management consultancy from banks', request: 'management consultancy tenders from banks, published in the last 2 weeks',
  status: 'complete',
  understood: { name: 'Management consultancy', meaning: 'Hiring consultants to advise a bank on strategy, operations or transformation.',
                summary: 'Looking for management consultancy for banks / financial institutions (strategy, operations, transformation), published 2–16 Nov.',
                buyers: ['banks'], window: 'published 2–16 Nov', likely: 9, not_this: ['engineering design consultancy'] },
  progress: [
    { at: '2026-11-16T13:40:09', text: 'Shortlist: 9 bids from 27 titles looked at (closing soonest first). Reading documents…' },
    { at: '2026-11-16T13:42:03', text: 'Read 9 of 9 bids: 31 files downloaded, 4 placeholders skipped, triage dropped 4, 3 quarantined after reading, 2 relevant so far…', kind: 'count' },
    { at: '2026-11-16T13:42:05', text: 'Done: 2 relevant, 7 quarantined.' }],
  results: RESULTS, zip_url: '#demo-zip', parent_run_id: null,
}

const RUNS: RunListItem[] = [
  { run_id: 'demo-1', title: 'Management consultancy from banks', when: '2026-11-16T13:40:00', found: 2, new_count: 0, saved_search: false },
  { run_id: 'demo-2', title: 'LOS tenders, last 2 weeks', when: '2026-11-16T10:05:00', found: 3, new_count: 1, saved_search: false },
  { run_id: 'demo-3', title: 'AML software for banks', when: '2026-11-16T08:00:00', found: 5, new_count: 2, saved_search: true },
]

export class MockApi implements TenderApi {
  private memory: MemoryFactView[] = [
    { id: 'm1', fact: 'Interested in bank, NBFC and financial-regulator IT tenders', evidence: 'kept 11 of 11 bids from such buyers', source: 'learned', since: '2026-11-18', kind: 'general', topic: '' },
    { id: 'm2', fact: 'LOS = Loan Origination System', evidence: 'downloaded the banking group twice; ignored Line of Sight', source: 'learned', since: '2026-11-19', kind: 'general', topic: '' },
    { id: 'm3', fact: 'We sell AML and LOS software to banks', evidence: 'you said so on 20 Nov', source: 'stated', since: '2026-11-20', kind: 'general', topic: '' },
  ]
  private delayMs: number
  constructor(delayMs = 700) {
    this.delayMs = delayMs
  }
  async me() { return { name: 'Asha Sharma', org: 'demo data' } }
  async login(name: string) { return { name, org: 'demo data' } }
  async listRuns() { return RUNS }
  async getRun(runId: string) { return { ...RUN, run_id: runId, title: RUNS.find(r => r.run_id === runId)?.title ?? RUN.title } }
  async getStatus(runId: string): Promise<RunStatusSummary> {
    const run = await this.getRun(runId)
    return { status: run.status, results: run.results.length, progress: run.progress.length }
  }

  /** Replays the sample run as a stream, so the streaming layout can be seen working. */
  ask(text: string, onEvent: (e: RunEvent) => void) {
    const started: RunView = { ...RUN, run_id: `demo-${Date.now()}`, title: text.slice(0, 60), request: text, status: 'running', understood: null, progress: [], results: [] }
    const at = (s: string) => s.replace('2026-11-16T13:4', '2026-11-16T13:5')
    const events: RunEvent[] = [
      { type: 'started', run: started },
      { type: 'understood', understood: RUN.understood! },
      ...RUN.progress.slice(0, 3).map(p => ({ type: 'progress', event: { ...p, at: at(p.at) } }) as RunEvent),
      ...RESULTS.map(bid => ({ type: 'result', bid }) as RunEvent),
      { type: 'progress', event: { ...RUN.progress[3], at: at(RUN.progress[3].at) } },
      { type: 'done', status: 'complete' },
    ]
    const timers = events.map((e, i) => setTimeout(() => onEvent(e), i * this.delayMs))
    return () => timers.forEach(clearTimeout)
  }
  async stop() {}
  async rule(_runId: string, bId: number, decision: 'counts' | 'not') {
    const b = RESULTS.find(x => x.b_id === bId)
    if (!b) return []
    const fact = decision === 'counts' ? `${b.question} counts` : `${b.question} does not count`
    return [{ ...b, state: decision === 'counts' ? 'relevant' as const : 'quarantined' as const, relevant: decision === 'counts',
      reason: decision === 'not' ? `Your ruling: ${fact}` : b.reason, ruling: { decision, by: 'you' as const, fact } }]
  }
  async unrule(_runId: string, bId: number) { return RESULTS.filter(x => x.b_id === bId) }
  async restore(_runId: string, bId: number) {
    return RESULTS.filter(x => x.b_id === bId).map(b => ({ ...b, state: 'relevant' as const, relevant: true, restored: true }))
  }
  async listMemory() { return this.memory }
  async forgetMemory(id: string) { this.memory = this.memory.filter(f => f.id !== id) }
  async editMemory(id: string, text: string) { this.memory = this.memory.map(f => f.id === id ? { ...f, fact: text, source: 'stated' } : f) }
}
