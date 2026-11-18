// The contract between the web chat and the FastAPI backend (spec 4.1, 5.2). Mirrors the backend's Pydantic models
// (agents/schemas.py: Verdict, Evidence, KeyFacts, MemoryFact; cache/docs.py: FileRecord). Dates are ISO strings
// in IST clock time, as the backend stores them.

export interface Evidence {
  quote: string
  file: string // the file name on GeM
  file_name: string // what the document is ("Scope of Work", the RFP's heading), as the files list names it
  page: number
  verified: boolean // the quote was found in that page of the document
  /** Why an unverified quote is not counted: under 4 words (it may well be there), or not found in any document. */
  check?: 'verified' | 'short' | 'not_found'
}

export interface KeyFacts {
  emd: string | null
  min_turnover: string | null
  contract_period: string | null
  estimated_value: string | null
  scope_summary: string | null
}

/** The last date for bidders' queries (agents/schemas.py QueriesDeadline): GeM's own pre-bid date when its bid
 * document prints one, else the date the Judge read in the buyer's documents, with the words it read. */
export interface QueriesDeadline {
  when: string // IST clock time
  source: 'gem_pre_bid' | 'documents'
  /** GeM's pre-bid date is the pre-bid meeting; an RFP may set an earlier last date for queries. */
  kind?: 'queries_deadline' | 'pre_bid_meeting'
  quote: string
  file: string // the file name on GeM
  file_name: string // what the document is, as the files list names it
  page: number
  verified: boolean // the words were found on that page
}

export type FileStatus = 'ok' | 'placeholder' | 'empty' | 'failed' | 'unsupported'

export interface FileInfo {
  name: string // what it is: its slot in the bid ("Scope of Work"), its own heading, else the file name (cache/names.py)
  title: string | null // its own heading
  also: string[] // other slots and file names the same document was uploaded under
  copies: number // identical uploads shown as this one
  filename: string // the file name on GeM
  pages: number
  status: FileStatus
  download_name: string // "GEM-2026-B-8229813 - Scope of Work.pdf"
  url: string // download link for this file
}

export interface BidResult {
  b_id: number
  bid_number: string
  title: string
  buyer: string
  buyer_org: string | null // the organisation from the bid document; the listing has none
  published: string
  closing: string
  relevant: boolean
  restored: boolean // the user moved it back from quarantine
  confidence: number
  reason: string
  evidence: Evidence[]
  key_facts: KeyFacts | null
  queries?: QueriesDeadline | null // none when the documents don't state one, or the verdict predates 17 Nov
  files: FileInfo[]
  stage?: 'triage' | 'judge' | 'deep' | null  // triage: ruled out from the title and first pages, no quotes
  /** What the quotes prove (shown instead of the model's own confidence): a verified quote from the documents beyond
   * the title, verified quotes from the title pages only, or none found. Null for triage. */
  proof?: 'documents' | 'title' | 'none' | null
  needs_review: boolean
  gem_url: string
  group: string // the meaning it was found for (two when a request stayed ambiguous)
  error: string // set when the bid's documents could not be read
}

export interface Understood {
  name: string // short name of the meaning, e.g. "Loan Origination System"
  meaning: string
  summary: string // one sentence, e.g. "Looking for management consultancy for banks, published 2–16 Nov."
  buyers: string[] // buyers the request named, e.g. ["banks"]
  window: string // e.g. "published 2–16 Nov"
  window_count: number // open bids in the window
  likely: number // titles that look relevant
  not_this: string[] // what the Judge rules out
}

export interface ProgressEvent {
  at: string
  text: string
  kind?: 'count' // the running count: shown as one line, the newest, updated in place
}

export type RunStatus = 'running' | 'complete' | 'failed' | 'stopped'

export interface RunView {
  run_id: string
  title: string
  request: string
  status: RunStatus
  understood: Understood | null
  progress: ProgressEvent[]
  results: BidResult[]
  zip_url: string
  parent_run_id: string | null
}

export interface RunListItem {
  run_id: string
  title: string
  when: string
  found: number
  new_count: number
  saved_search: boolean
}

export interface MemoryFactView {
  id: string
  fact: string
  evidence: string
  source: 'stated' | 'learned'
  since: string
}

export interface UserView {
  name: string
  org: string
}

/** What the backend streams (server-sent events) while a run works. */
export type RunEvent =
  | { type: 'started'; run: RunView }
  | { type: 'understood'; understood: Understood }
  | { type: 'progress'; event: ProgressEvent }
  | { type: 'result'; bid: BidResult }
  | { type: 'done'; status: RunStatus }
  | { type: 'message'; text: string } // an answer that needs no search
  | { type: 'show'; tab: 'relevant' | 'quarantine' | 'memory' }

/** Thrown by me() when the browser is not signed in. */
export class SignInRequired extends Error {
  constructor() {
    super('sign in first')
  }
}

export interface TenderApi {
  me(): Promise<UserView>
  login(name: string): Promise<UserView>
  listRuns(): Promise<RunListItem[]>
  getRun(runId: string): Promise<RunView>
  /** Starts a run (or refines the current one) and streams its events. Returns a function that stops listening. */
  ask(text: string, onEvent: (e: RunEvent) => void, runId?: string): () => void
  /** Stops a running run; results judged so far are kept. */
  stop(runId: string): Promise<void>
  restore(runId: string, bId: number): Promise<void>
  listMemory(): Promise<MemoryFactView[]>
  forgetMemory(factId: string): Promise<void>
  editMemory(factId: string, text: string): Promise<void>
}
