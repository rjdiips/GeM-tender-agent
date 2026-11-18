// PROTOTYPE sample data. Bid numbers, titles, buyers and dates are real (local index, 16 Nov 2026).
// The verdict for GEM/2026/B/8162911 is the real Judge output; the other verdicts are invented for layout only.

export type Evidence = { quote: string; file: string; page: number; verified: boolean }
export type Bid = {
  bidNumber: string; title: string; buyer: string; published: string; closing: string
  relevant: boolean; confidence: number; reason: string; evidence: Evidence[]
  emd?: string; turnover?: string; period?: string
  files: { name: string; pages: number; status: 'ok' | 'placeholder' }[]
}

export const request = 'management consultancy tenders from banks, published in the last 2 weeks'

export const understood =
  'Looking for management consultancy for banks / financial institutions (strategy, operations, transformation), ' +
  'published 2–16 Nov. 36,104 open bids in that window; 9 look relevant from their titles.'

export const progress = [
  understood,
  'Shortlist: 9 bids from 27 titles looked at (closing soonest first). Reading documents…',
  'Read 9 of 9 bids: 31 files downloaded, 4 placeholders skipped, triage dropped 4, 3 quarantined after reading, 2 relevant so far…',
  'Done: 2 relevant, 7 quarantined.',
]

export const bids: Bid[] = [
  {
    bidNumber: 'GEM/2026/B/8263340', title: 'Hiring of Consultants - Milestone/Deliverable Based - Management Consultants, Functional Consultant',
    buyer: 'Ministry of Finance / Department of Financial Services', published: '11 Nov 14:22', closing: '18 Nov 15:00',
    relevant: true, confidence: 0.86, reason: 'A public sector bank is hiring functional consultants to redesign retail lending processes.',
    evidence: [{ quote: 'The consultant shall assist the Bank in re-engineering its retail credit processes and branch operating model', file: 'RFP_Consultancy.pdf', page: 12, verified: true }],
    emd: 'Rs 10,00,000', turnover: 'Rs 50 crore', period: '18 months',
    files: [{ name: 'GeM-Bidding-10133210.pdf', pages: 6, status: 'ok' }, { name: 'RFP_Consultancy.pdf', pages: 64, status: 'ok' }],
  },
  {
    bidNumber: 'GEM/2026/B/8162911', title: 'Hiring of Consultants - Milestone/Deliverable Based - Management Consultants, AS PER RFP; Business Transformation Expert',
    buyer: 'Ministry of Finance / Department of Financial Services', published: '14 Oct 11:02', closing: '23 Nov 16:00',
    relevant: true, confidence: 1.0, reason: 'The tender hires management consultants for organisational and business transformation within a public sector bank.',
    evidence: [
      { quote: 'The scope of Project Muskaan shall encompass Retail Operations and Branch Banking across both asset and liability products, including centralization of activities', file: 'MUSKANrfp.pdf', page: 30, verified: true },
      { quote: 'Bidder should have experience of minimum 5 years in providing the Services in field of consultancy in organizational transformation', file: 'MUSKANrfp.pdf', page: 37, verified: true },
    ],
    emd: 'Rs 2,10,00,000', turnover: 'Rs 200 crore', period: '2 years',
    files: [{ name: 'GeM-Bidding-10037444.pdf', pages: 7, status: 'ok' }, { name: 'MUSKANrfp.pdf', pages: 112, status: 'ok' },
            { name: '1791988256.pdf', pages: 1, status: 'placeholder' }, { name: 'SLA (bidsla)', pages: 1, status: 'ok' }],
  },
  {
    bidNumber: 'GEM/2026/B/8264466', title: 'Financial Audit Services - Concurrent Audit of Digital Banking Operations; CA Firm',
    buyer: 'Ministry of Finance / Department of Financial Services', published: '16 Nov 12:01', closing: '7 Dec 17:00',
    relevant: false, confidence: 0.8, reason: 'Concurrent audit by a CA firm: audit services are related but not management consultancy.',
    evidence: [{ quote: 'Appointment of Chartered Accountant firms for concurrent audit of digital banking operations', file: 'Audit_Scope.pdf', page: 2, verified: true }],
    files: [{ name: 'GeM-Bidding-10164020.pdf', pages: 5, status: 'ok' }, { name: 'Audit_Scope.pdf', pages: 14, status: 'ok' }],
  },
  {
    bidNumber: 'GEM/2026/B/8269932', title: 'Cyber Security Audit - CSCRF', buyer: 'Ministry of Finance / Department of Financial Services',
    published: '12 Nov 22:30', closing: '19 Nov 09:00', relevant: false, confidence: 0.85,
    reason: 'A cyber security audit under SEBI CSCRF, not a consultancy engagement.',
    evidence: [{ quote: 'Conduct of cyber security audit in line with the SEBI Cybersecurity and Cyber Resilience Framework', file: 'GeM-Bidding-10170330.pdf', page: 3, verified: true }],
    files: [{ name: 'GeM-Bidding-10170330.pdf', pages: 5, status: 'ok' }],
  },
  {
    bidNumber: 'GEM/2026/B/8268156', title: 'Entry and Mid Level Desktop Computer', buyer: 'Ministry of Finance / Department of Financial Services',
    published: '12 Nov 15:40', closing: '16 Nov 16:00', relevant: false, confidence: 0.9, reason: 'Desktop computers (triage).',
    evidence: [], files: [{ name: 'GeM-Bidding-10166400.pdf', pages: 4, status: 'ok' }],
  },
]

export const history = [
  { title: 'management consultancy from banks', when: 'today 13:40', found: 2, isNew: 0 },
  { title: 'LOS tenders, last 2 weeks', when: 'today 10:05', found: 3, isNew: 1 },
  { title: 'AML software for banks (daily 08:00)', when: 'saved search', found: 5, isNew: 2 },
]

export const memory = [
  { fact: 'Interested in bank, NBFC and financial-regulator IT tenders', evidence: 'kept 11 of 11 bids from such buyers', source: 'learned', since: '18 Nov' },
  { fact: 'LOS = Loan Origination System', evidence: 'downloaded the banking group twice; ignored Line of Sight', source: 'learned', since: '19 Nov' },
  { fact: 'We sell AML and LOS software to banks', evidence: 'you said so on 20 Nov', source: 'stated', since: '20 Nov' },
]
