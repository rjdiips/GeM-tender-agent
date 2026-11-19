# GeM Tender Agent

The language of the tender chatbot and the GeM bids it finds, reads and judges.

## Language

**Bid**:
One procurement listing on GeM, identified by its bid number (`GEM/YYYY/B/n`).
_Avoid_: Tender, listing, opportunity

**Corrigendum**:
An official amendment the buyer publishes to a bid after it opens, most often extending its closing date.
_Avoid_: Amendment, addendum, update

**Topic definition**:
The written-out meaning of the user's topic: what it is, the terms that signal it, what is related but not enough, and what it is not.
_Avoid_: Query expansion, keywords

**Verdict**:
The Judge's decision on whether one bid matches one topic definition, with a reason and quotes from the bid's documents.
_Avoid_: Decision, classification, label

**Quarantine**:
Where bids judged not relevant are held for 30 days, so the user can restore a wrong rejection.
_Avoid_: Rejected, trash, bin
