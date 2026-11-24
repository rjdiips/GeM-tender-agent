# Repairs: wrong results, disk writes, honest progress

Status: approved 24 Nov 2026. This is the first of two plans. The second will change how bids are decided: each bid is profiled once and matched against topics.

## Why

A review on 23 Nov 2026 ran live searches and analysed the stored runs. It found bugs that give wrong or silently missing results, avoidable writes to the shared spinning disk (see the server's disk notice), and progress lines that mislead users. These repairs fix all three without changing how bids are decided.

## Goals

- No silent loss of results: a bid or search that could not be read is shown as such.
- Filters do what the user asked.
- Fewer writes to disk per run, and a bounded cache.
- Progress and summary lines that a non-engineer reads correctly.

## Non-goals

- The title screen prompt, the "own search first" result order, and the stage-2 search budget. The second plan replaces all three.
- Profiling bids once per bid instead of once per (bid, topic). That is the second plan.

## Anti-goals

- Any new background job that runs more often than hourly, or any fast retry loop.
- A change that makes the screen or the Judge drop more bids. Here, precision comes only from fixing bugs.

## Approaches

Two were considered:

- **Targeted fixes in place (chosen).** Each defect is fixed where it lives, with a test. This keeps the current pipeline, so the second plan starts from a correct baseline.
- **Fold all fixes into the redesign.** Rejected: the redesign is weeks away, and the screen fail-open, the filter bugs and the disk writes hurt now.

## Decisions

1. **Date window** (`agents/schemas.py` `DateWindow.resolve`, `agents/chat.py`).
   - If the request text names a month or a calendar date, the model's from/to dates win.
   - Otherwise, "last N days" wins.
   - `last_days=0` means today, not the 14-day default.
   - A closing window with only a from-date stays open-ended.
   - The decision is made in code from the request text.
2. **Buyer-only.** The hard buyer filter applies only when the request contains "only" or "exclusively", checked in code. The model's flag alone is not enough.
3. **`limit`.** Removed from the model schema and from `TenderRequest`. It was never used, and the model invented values for it.
4. **Failed downloads.**
   - A failed main bid document makes the outcome an error ("could not be read"). It is no longer triaged on its title, and no verdict is cached.
   - `is_real_error` treats 400, 401, 410 and 422 as errors.
   - A cached document set is "fresh" only if it has no failed file apart from GTC.
5. **Caches.**
   - A base search cut short by the page budget is never stored as complete.
   - The verdict cache key adds the model id and a prompt-version constant, which is bumped when the triage or judge prompts change.
   - The verdict and document caches write temp files with unique names.
   - An unreadable or schema-drifted verdict file counts as a miss.
6. **Title screen stop-gap.**
   - A batch that fails is split in half and retried once per half.
   - Only a batch that still fails keeps its titles, marked "screen failed".
   - Other batches' answers stand.
7. **Disk.**
   - Outcomes are stored as append-only `outcomes.jsonl`, one line per change, last line wins.
   - The old `outcomes.json` is still read, for stored runs.
   - The rate limiter writes its state file only when the interval changes.
   - A retention job (`jobs/retention.py`, `gem-agent retention`) prunes the document cache using the existing `RETAIN_*` settings. It is run by hand or at most hourly.
8. **Reporting.**
   - Full-text searches skipped for having more than 20,000 hits get a progress line naming the term.
   - The probe's counts cover only the date window.
   - The "open bids" number is dropped from the card.
9. **UI and progress lines.**
   - Lines in plain words, with no engineering terms ("triage", "placeholders", internal budgets).
   - Finished steps have no trailing "…", and "1 bid" is singular.
   - A stopped run gets a closing line: what was read, and what was not.
   - The Relevant tab shows an honest empty state once bids have been read.
   - The status dot has distinct colours for running, stopped and failed.
   - The layout works at phone width: the sidebar collapses and nothing scrolls sideways.
   - The browser's 3-second poll fetches only the run's status and counts, not every result.
10. **Docs.** `docs/APP_FLOW.md` and `docs/OVERVIEW.md` describe live search, not the removed index and timers.

## Constraints

- **Shared spinning disk.** Check `/proc/pressure/io` before running live tests, and run one live search at a time.
- **Commits** are authored as rjdiips, as AGENTS.md requires.
- **`data/runs` is tracked in git**, so new run files must stay text and diff cleanly.
- **The Qwen model behind vLLM** enforces JSON schemas, so schema changes must keep every field required.

## Left to the builder

- Exact wording of the progress lines, within the rules in decision 9.
- How month and date names are detected in the request text (a small regex is enough).
- How phone-width layout is laid out (a drawer or a tab bar for the sidebar).
