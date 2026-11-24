// Test-only TenderApi: answers at once and records calls.
import { SignInRequired, type BidResult, type MemoryFactView, type RunEvent, type RunView, type TenderApi } from '../api/types'

export const NOW = '2026-11-16T13:42:00'

export function bid(over: Partial<BidResult> = {}): BidResult {
  return {
    b_id: 1, bid_number: 'GEM/2026/B/8162911', title: 'Hiring of Consultants - Management Consultants',
    buyer: 'Ministry of Finance / Department of Financial Services', buyer_org: 'Union Bank Of India', published: '2026-10-14T11:02:00',
    closing: '2026-11-23T16:00:00', relevant: true, state: 'relevant', question: '', ruling: null, restored: false, confidence: 1, reason: 'Hires management consultants for a bank.',
    evidence: [{ quote: 'The scope of Project Muskaan shall encompass Retail Operations', file: 'MUSKANrfp.pdf',
                 file_name: 'RFP for Project Muskaan', page: 30, verified: true }],
    key_facts: { emd: 'Rs 2,10,00,000', min_turnover: 'Rs 200 crore', contract_period: '2 years', estimated_value: null, scope_summary: null },
    files: [{ name: 'RFP for Project Muskaan', title: 'RFP for Project Muskaan', also: ['Scope of Work', 'MUSKAN2.pdf'],
              copies: 2, filename: 'MUSKANrfp.pdf', pages: 112, status: 'ok',
              download_name: 'GEM-2026-B-8162911 - RFP for Project Muskaan.pdf', url: '/api/files/1' },
            { name: '1791988256.pdf', title: null, also: [], copies: 1, filename: '1791988256.pdf', pages: 1,
              status: 'placeholder', download_name: 'GEM-2026-B-8162911 - 1791988256.pdf', url: '/api/files/2' }],
    proof: 'documents', needs_review: false, gem_url: 'https://bidplus.gem.gov.in/showbidDocument/10037444', group: 'Management consultancy', error: '', ...over,
  }
}

export function run(over: Partial<RunView> = {}): RunView {
  return {
    run_id: 'r1', title: 'Management consultancy from banks', request: 'management consultancy tenders from banks',
    status: 'complete',
    understood: { name: 'Management consultancy', meaning: 'Consultants advising banks', window: 'published 2–16 Nov',
                  summary: 'Looking for consultants advising banks, published 2–16 Nov.', buyers: ['banks'],
                  likely: 9, not_this: ['engineering consultancy'] },
    progress: [{ at: '2026-11-16T13:40:09', text: 'Shortlist: 9 bids.' }, { at: '2026-11-16T13:42:05', text: 'Done: 1 relevant, 1 quarantined.' }],
    results: [bid(), bid({ b_id: 2, bid_number: 'GEM/2026/B/8264466', title: 'Concurrent Audit of Digital Banking', relevant: false, state: 'quarantined',
                           reason: 'Audit, not consultancy.', closing: '2026-12-07T17:00:00', evidence: [], stage: 'triage' })],
    zip_url: '/api/runs/r1/zip', parent_run_id: null, ...over,
  }
}

export class FakeApi implements TenderApi {
  calls: string[] = []
  memory: MemoryFactView[] = [
    { id: 'f1', fact: 'We sell AML and LOS software to banks', evidence: 'you said so on 20 Nov', source: 'stated', since: '2026-11-20', kind: 'general', topic: '' },
    { id: 'f2', fact: 'LOS = Loan Origination System', evidence: 'downloaded the banking group twice', source: 'learned', since: '2026-11-19', kind: 'general', topic: '' },
  ]
  script: RunEvent[] = []
  current = run()
  signedIn = true
  live: ((e: RunEvent) => void) | null = null

  async me() {
    if (!this.signedIn) throw new SignInRequired()
    return { name: 'Asha Sharma', org: 'finsurge' }
  }
  async login(name: string) { this.calls.push(`login ${name}`); this.signedIn = true; return { name, org: 'finsurge' } }
  async listRuns() {
    return [{ run_id: 'r1', title: 'Management consultancy from banks', when: '2026-11-16T13:40:00', found: 1, new_count: 0, saved_search: false },
            { run_id: 'r0', title: 'AML software for banks', when: '2026-11-16T08:00:00', found: 5, new_count: 2, saved_search: true }]
  }
  async getRun(runId: string) { this.calls.push(`getRun ${runId}`); return this.current }
  ask(text: string, onEvent: (e: RunEvent) => void, runId?: string) {
    this.calls.push(`ask ${text}${runId ? ` (${runId})` : ''}`)
    this.live = onEvent
    this.script.forEach(onEvent)
    return () => { this.live = null }
  }
  async stop(runId: string) {
    this.calls.push(`stop ${runId}`)
    this.live?.({ type: 'done', status: 'stopped' })
  }
  /** What the next rule/unrule/restore call returns as changed bids. */
  changed: BidResult[] = []
  /** While set, rule/unrule/restore wait for it; `fail` makes them reject (as a 502 would). */
  hold: Promise<void> | null = null
  fail = false
  private async settle() {
    if (this.hold) await this.hold
    if (this.fail) throw new Error('POST: 502')
  }
  async rule(runId: string, bId: number, decision: 'counts' | 'not') { this.calls.push(`rule ${runId} ${bId} ${decision}`); await this.settle(); return this.changed }
  async unrule(runId: string, bId: number) { this.calls.push(`unrule ${runId} ${bId}`); await this.settle(); return this.changed }
  async restore(runId: string, bId: number) {
    this.calls.push(`restore ${runId} ${bId}`)
    await this.settle()
    return this.changed.length ? this.changed : [bid({ b_id: bId, bid_number: 'GEM/2026/B/8264466', relevant: true, state: 'relevant', restored: true })]
  }
  async listMemory() { return this.memory }
  async forgetMemory(id: string) { this.calls.push(`forget ${id}`); this.memory = this.memory.filter(f => f.id !== id) }
  async editMemory(id: string, text: string) { this.calls.push(`edit ${id} ${text}`) }
}
