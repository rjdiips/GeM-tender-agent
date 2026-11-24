# Describe each bid once, match descriptions, keep today's path as a safety net

Status: approved 24 Nov 2026. This is the second of two plans; the first is
`docs/superpowers/specs/2026-11-24-repairs-design.md` and lands before this one.

## Why

The users build a shortlist of tenders to bid on. **A tender missing from the list is lost revenue**; a slower run
or extra reading is only a cost. Today every bid is judged afresh for each request's topic. The same "Hiring of Agency
for IT Projects" bid is downloaded and read again for AML, then core banking, then fraud. Its subject is decided
from its title and first pages, and that misses banking tenders hidden behind vague titles.

## Evidence (offline test, 24 Nov 2026, `eval/describe_ab/`)

The test ran on 261 labelled bids, all with documents already in the cache. It sent no GeM requests.

- **Descriptions are good and cheap in model time.**
  - One call per bid, median 2.3 s.
  - All 261 were specific, e.g. "Enterprise KYC, KYB, CKYC & AML Solution for NABARD" for a bid titled "Hiring of Agency for IT Projects".
  - 258 of 261 had a scope or RFP section to write from.
- **Recall on the 13 subject topics** (37 real matches, 18 of them labels a person has checked):

  | Way | Checked matches missed | All missed | Non-matches fully read |
  |---|---|---|---|
  | Today (title screen, then triage) | 4 | 11 | 20 / 354 |
  | A: the model matches descriptions | 2 | 8 | 58 / 354 |
  | Today **or** A | 1 | 6 | 67 / 354 |
  | B: similarity | needs ~30% of bids read to miss none | | |

- **Today misses** vague-title banking bids (NABARD KYC/AML, Bank of India CMU).
- **A misses** a different kind: "Credit Card Management Software" for *payments* (in both runs), and an EFRM module inside a broader "risk and compliance suite" (in one run).
- **Answers vary between runs** at temperature 0. A missed 14 in one run and 8 in the next.
- **The pools were already title-plausible.** Live runs also carry junk (suitcases, cab hire), which a description disposes of.

## Goals

- **Fewer misses than today.** Target: no more checked-label misses than "Today or A" in the harness.
- **Each bid is described and read once,** and the result is reused by every later request and user. Later searches are fast. A slow first search after a quiet spell is accepted by the user.
- **A becomes safe enough on its own** to retire the safety net, proven by a repeatable harness.

## Non-goals

- Describing bids outside a search: no background or nightly job. The machine is not always on, and the disk is shared.
- Dropping bids on a similarity score. Similarity only orders work.
- Changing the Judge's verdict, its quotes, or its verdict cache, beyond the key change in plan 1.

## Anti-goals

- **Any path where a bid is dropped because only one check said no.** While the safety net is on, a bid is dropped only when A says "no" **and** today's path says no.
- **Retiring the safety net silently.** It is a setting, and only a person turns it off.

## Approaches considered

1. **A with today's path as a safety net, and A tuned in a harness (chosen).** A bid is read fully if its description matches or is unsure, or if today's title screen plus triage would read it. A harness replays A on cached descriptions, so prompt changes are re-scored in seconds.
2. **A alone, tuned first.** Rejected for now. It misses tenders until the tuning holds, and every miss costs revenue.
3. **B (similarity) to drop bids.** Rejected. To keep every match it needs about 30% of bids read, and its last-ranked bids include real matches. It is kept only for ordering.

## Design

### 1. The description store

- **Table:** `descriptions` in `data/bids/bids.sqlite`, one row per bid. Columns:
  - `b_id`
  - `doc_hash`: as in `agents/judge.py`, so a corrigendum or new attachment makes the row stale.
  - `subject`
  - `terms`: a JSON list, up to 8.
  - `sources`: the file names read.
  - `has_scope`
  - `model`, `prompt_version`
  - `written_at`
- **A row is current** when its `doc_hash` matches the bid's cached documents, and its model and prompt version match the running ones. A stale row is rewritten the next time a search touches the bid.
- **The prompt** is the spike's DESCRIBE prompt. It is the card writer's prompt widened from vague titles to every bid. It reads the bid document (and an RA's original bid) plus up to 3 attachments: Scope of Work first, then the largest.
- **Existing cards** are copied in as stale rows (`prompt_version="card"`), so they are refreshed when next touched.
- **The Searcher's title screen** shows a current description only for vague titles, as it shows cards today.
  - It must not show one for every bid. The safety net is worth having only while it stays independent of A, and the harness measures exactly this screen. *(Builder-check call.)*
- **The end-of-run card writer** (`report` → `write_cards`) is removed.
- **Staleness is checked only when a bid's documents are collected.**
  - If the new `doc_hash` differs, the description is rewritten.
  - A search never downloads documents just to check a description.
  - A `prompt_version` bump does not invalidate rows: a bid is re-described the next time its documents are collected, or when `gem-agent describe --refresh` is run by hand.
- **Retention and descriptions never conflict.** Retention removes documents only for bids closed 30+ days ago, and searches return only open bids. Description rows are kept as long as the bid cache keeps the bid (30 days after closing). *(Builder-check call.)*
- **A failed description is not stored.** The bid goes through today's path this run, and a description is tried again on the next search. A bid whose bid document fails to download is an error, as in plan 1.

### 2. A run, step by step

`plan` and `search` stay as they are: title screen, window and buyer filters. Then `process` changes:

1. **Describe.** Every bid in the in-window pool gets a description, unless a current one exists.
   - The pool is every in-window search hit, not only the bids the title screen kept. A must see what the screen drops, because that is where today misses tenders.
   - This downloads each new bid's documents once, and they are reused later by the Judge.
   - Bids are taken in this order:
     - first, bids the topic's own searches found;
     - then bids the title screen kept;
     - then by closing date, soonest first;
     - ties broken by B similarity of the title to the topic.
2. **Match (A).** Per topic, descriptions are matched in batches of up to 40: match / unsure / no.
   - Each line A sees is the description plus the buyer: ministry, department and the buyer organisation named in the documents.
   - This lets A decide buyer topics such as "Ministry of Finance tenders" as well. There is no separate code path for buyer topics.
   - The harness scores buyer topics too, separately. *(Builder-check call.)*
   - A batch is sent once 40 descriptions are ready, or when the describe queue is empty, so the first results do not wait for the whole pool.
   - A failed batch is split and retried, as in plan 1's screen fix. Bids in a batch that still fails count as "unsure".
3. **Safety net.** This is only for bids where A says "no" (while the net is on).
   - If the title screen had kept the bid, it gets today's triage on its bid document.
   - It is read fully if triage does not say clearly no.
4. **Read.** Bids are fully read in this order:
   - A "match";
   - then A "unsure";
   - then bids only the safety net kept.
   - Within each group, closing soonest first.

   The existing Judge then runs: passages, verdict and checked quotes.
5. **Not read.** A bid dropped by both checks goes to Quarantine with the reason "Not a match: buys <subject>", plus which checks said no. It can be restored as today.

### 3. Limits per run

- **Full reads per topic:** capped at 150, as today, with the "N more not read" line.
- **New descriptions per request:** capped at 300, filled from one queue across all of the request's topics, in the order given in section 2. Each one costs a download at GeM's 1 request per second. Bids left undescribed are reported in a line, "N bids not yet described; they will be checked on the next search", and are **not** dropped.
  - Undescribed bids go through today's path in full: the title screen, then triage. They do so even with the safety net off, since A cannot judge them.
- **Paging** (the stage-2 changes plan 1 deferred to this plan):
  - Stop paging an all-bids search once a page's bids were all published before the window start. Those searches are sorted newest first.
  - Count the page budget after the window filter.
  - Give each search an equal share of the budget, instead of letting the base searches spend it first.

### 4. The safety net setting

- **Setting:** `MATCH_SAFETY_NET=true` (Settings `match_safety_net: bool = True`).
- **Off** means A's "no" alone drops a bid. That is still only when the description has `has_scope`; otherwise the bid goes through today's path.

### 5. The tuning harness

- **Command:** `gem-agent eval match`, which replaces the spike.
- **What it does:**
  - Reuses current descriptions for the labelled bids, writing any that are missing from cached documents only. It sends no GeM requests.
  - Runs A, today's path and "today or A".
  - Repeats the run 3 times to show the run-to-run spread.
- **Report:**
  - Per way: checked-label misses, all misses, non-matches read, and model calls.
  - The missed tenders, with their descriptions.
- **Buyer topics** such as "Ministry of Finance tenders" are reported separately, because a description cannot decide them.
- **Retiring the safety net** is recommended only when A alone misses no checked label in 3 runs in a row on the current prompt. The harness prints whether that holds. A person still flips the setting.
- **The first round of tuning is part of this build.** The user asked for A to be tuned. It covers:
  - the match prompt;
  - the two misses the evidence shows (card management under payments, and a module inside a broader suite);
  - an "unsure" rule for "a product in the same family as the topic".

  Each change is scored with the harness, and kept only if it misses no more than the version before. Further rounds are routine work after this build.
- **Trusting the harness depends on the labels.** It needs the 23 labels flagged for review checked by a person, and the AI topic's definition reconciled with its borderline labels (AI CCTV and AI servers). Until then, the harness reports checked labels and all labels separately.

### 6. UI

- **Quarantine reasons** for bids not read show the description and which checks said no.
- **A fully read bid** shows "Read because: matches / might match / kept by the title check" under its verdict.
- **Progress lines** follow plan 1's rules. New lines:
  - `Described {n} new bids ({m} already known).`
  - `Matched {n} bids: {a} match, {b} might.`
  - The "not yet described" line from section 3.

## Constraints

- **Shared spinning disk** (see the server notice):
  - The first slow search downloads documents for every new bid in its pool, at about 1 request a second.
  - Plan 1's retention job bounds the cache.
  - No new periodic job.
- **GeM:** all traffic is still serialised through the shared rate limiter. Describing adds no parallelism there.
- **vLLM is shared:** describing runs at most 3 model calls at a time.
- **Model-facing schemas keep every field required.**
- **Commits** are authored as rjdiips, as AGENTS.md requires.
- **Plan 1 is built and merged before this plan is built.** This plan is written against the interfaces plan 1's plan names (`docs/superpowers/plans/2026-11-24-repairs.md`); if plan 1's build changes one, this plan is corrected before it starts. It adds a `read_because` field (`"match" | "unsure" | "net"`, or none) to `Outcome`. This design relies on these of plan 1's fixes:
  - failed downloads becoming errors;
  - outcomes stored as `outcomes.jsonl`;
  - the screen batch split;
  - the verdict cache version.

## Left to the builder

- The schema migration mechanics for the new table.
- How the match batching flushes in code.
- The wording of the new progress lines, within plan 1's rules.
- How the harness prints its report.
