# Live GeM Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local bid index and its systemd timers with live GeM searches made when the user asks, so nothing runs in the background and the shared disk is left alone.

**Architecture:** A `LiveSearcher` runs typed GeM searches (category, `exact` buyer, `fullText` term) through the existing rate-limited session. It runs in two stages: page 1 of every search first, then the remaining pages. The Searcher always adds four base searches, screens titles with the existing model call, and keeps the bids it shortlists, with their cards, in a small SQLite bid cache. The run graph searches, judges, searches deeper, and judges again. The Chat step checks each meaning against page 1 of live searches instead of the index.

**Tech Stack:** Python 3.11, httpx (existing `BidplusSession` / `ClientPool`), pydantic / pydantic-ai, LangGraph, SQLite (existing `BidIndex` class, reused as the bid cache), pytest with `FakeGem` and `ScriptedLLM`.

**Spec:** `docs/SPEC-GEM_TENDER_AGENT.md`, section 6.2 "Live search" (the design), with 3.2 (GeM search behaviour), 5.2, 5.3, 8, 11 and 13. Measurements are in `eval/live_search/`.

## Global Constraints

- **GeM request spacing:** unchanged. The existing `AdaptiveRateLimiter` floor is ≥1 s, or 4 s after errors (spec 3.2). Never bypass `BidplusSession`.
- **Skip threshold:** `fullText` searches with `num_found > 20000` are skipped (`SKIP_OVER = 20000`).
- **Base search cache:** TTL `timedelta(hours=1)`.
- **Bid cache pruning:** rows whose `closing` is more than `timedelta(days=30)` in the past.
- **Model-facing schemas:** every field required, free text bounded (rules in spec, build step 5 and the 16 Nov rule).
- **Disk:**
  - No whole-file rewrite of a growing file.
  - Cache files are written atomically: temporary file, then `os.replace`.
  - No DEBUG logging left on. `logging.getLogger("httpx")` is set to WARNING.
- **Offline tests** (`pytest -m "not live"`) must not reach the network. GeM is faked with `FakeGem` from `tests/conftest.py`.

## Review Focus

1. **A run interrupted between stage 1 and stage 2 resumes at stage 2.** It must not re-read stage 1 or re-judge bids in `done_ids`. Test: Task 6, `test_a_resumed_run_continues_with_the_second_stage`.
2. **A GeM error partway through one search must not fail the run.** That search is marked incomplete and the others carry on. Test: Task 1, `test_a_search_that_fails_midway_keeps_its_pages_and_the_run_goes_on`.
3. **A request whose terms all match 0 bids still gets the base searches,** and the shortlist is not empty only because the plan was poor. Test: Task 5, `test_base_searches_run_even_when_the_plan_has_no_categories_or_buyers`.
4. **A cache file from before a restart, more than an hour old, is not used.** A corrupt or partly written cache file is ignored, not fatal. Test: Task 1, `test_an_old_or_unreadable_cache_file_is_ignored`.
5. **A bid found by both stages is judged once.** Test: Task 5, `test_the_second_stage_returns_only_bids_the_first_did_not`.

---

## File structure

| File | Responsibility |
|---|---|
| `gem_agent/tools/gem/live.py` (create) | `SearchSpec`, `SearchProgress`, `SearchOutcome`, `LiveSearcher`: running typed searches in two stages, de-duplication, skipping, the base-search cache |
| `gem_agent/cache/search_cache.py` (create) | `SearchCache`: one JSON file per cached search, 1 h TTL, atomic writes |
| `gem_agent/index/db.py` (modify) | `upsert` writes only new or changed rows; add `prune(closed_before)` |
| `gem_agent/index/migrate.py` (create) | `migrate_bids(old, new, keep_ids)`: the one-off copy of cards and labelled bids |
| `gem_agent/agents/searcher.py` (rewrite) | live Searcher: base searches, plan searches, window filter, screen, bid cache |
| `gem_agent/agents/probe.py` (create) | `probe_live`: the Chat step's meaning check from page 1 of live searches |
| `gem_agent/agents/chat.py` (modify) | async probe; the broad-class instruction |
| `gem_agent/index/cards.py` (modify) | `write_cards` takes an explicit bid list |
| `gem_agent/graph/state.py`, `gem_agent/graph/build.py`, `gem_agent/graph/services.py` (modify) | two search stages; cards after a run |
| `gem_agent/app/factory.py`, `gem_agent/cache/lookups.py`, `gem_agent/config.py`, `gem_agent/cli.py`, `gem_agent/eval/run.py` (modify) | wiring; `bids migrate` replaces `index`; httpx at WARNING |
| deleted | `gem_agent/index/sync.py`, `gem_agent/index/query.py`, `gem_agent/index/probe.py`, `tests/index/test_sync.py`, `tests/index/test_query.py`, `tests/index/test_probe.py`, `deploy/systemd/*` |

---

### Task 1: LiveSearcher and the search cache

**Files:**
- Create: `gem_agent/tools/gem/live.py`
- Create: `gem_agent/cache/search_cache.py`
- Create: `tests/tools/test_live.py`
- Create: `tests/tools/live_helpers.py`

**Interfaces:**
- Consumes: `BidplusSession`, `list_all_bids`, `search_bids`, `AllBidsQuery`, `CategorySearch`, `SearchPage` from `gem_agent/tools/gem/search.py`; `Bid.from_listing`, `Bid.raw`, `site_now`.
- Produces:
  - `SearchSpec(kind: Literal["category", "exact", "fullText"], value: str)`, a frozen pydantic model, with `.key -> str` returning `f"{kind}|{value}"`.
  - `SearchProgress(num_found: int, pages_read: int, page_count: int, skipped: bool = False, same_as: str | None = None, failed: bool = False)`, a pydantic model. It is what RunState stores between stages.
  - `SearchOutcome(spec: SearchSpec, progress: SearchProgress, bids: list[Bid])`, a dataclass.
  - `LiveSearcher(session: BidplusSession, *, cache: SearchCache | None = None, cached_keys: frozenset[str] = frozenset(), skip_over: int = 20000)`, with:
    - `async first_pages(specs: list[SearchSpec]) -> list[SearchOutcome]`: page 1 of each spec, in order.
    - `async rest(specs: list[SearchSpec], progress: dict[str, SearchProgress]) -> list[SearchOutcome]`: pages `pages_read + 1 … page_count` of each spec that is not skipped, not `same_as` and not complete.
  - `SearchCache(root: Path, *, ttl: timedelta = timedelta(hours=1), now: Callable[[], datetime] = site_now)`, with:
    - `get(key: str) -> tuple[int, list[dict]] | None`: `(num_found, raw listing docs)`.
    - `put(key: str, num_found: int, docs: list[dict]) -> None`.

- [x] **Step 1: Write the fake site helper** `tests/tools/live_helpers.py: FakeSearchSite(by_query: dict[str, list[dict]])`.
  - It is a callable handler for `FakeGem.add`.
  - It decodes the form field `payload` (JSON) and picks the rows:
    - for `all-bids-data`: `by_query[f"{param.searchType}|{param.searchBid}"]`
    - for `search-bids` with `searchType == "bidNumber"`: `by_query[f"category|{category}"]`
  - It serves 10 rows per page with `numFound = len(rows)`, and the "No data found" 404 body past the end or for an unknown query, the same shape as `tests/index/helpers.FakeListing`.
  - It records `(key, page)` in `self.served`.
  - Rows come from `tests/index/helpers.row(b_id, published, closing, title, department=...)`.

- [x] **Step 2: Write the failing tests** in `tests/tools/test_live.py`:

```python
async def test_every_page_of_a_search_is_read(gem, clock):
    # 25 rows for fullText|aml -> first_pages reads page 1 (10 bids); rest reads pages 2-3
    first = await live.first_pages([SearchSpec(kind="fullText", value="aml")])
    assert first[0].progress == SearchProgress(num_found=25, pages_read=1, page_count=3)
    more = await live.rest([spec], {spec.key: first[0].progress})
    assert len(first[0].bids) + len(more[0].bids) == 25 and more[0].progress.pages_read == 3

async def test_a_search_whose_first_page_repeats_an_earlier_one_is_read_once(gem, clock):
    # fullText|payment gateway and fullText|payment hub serve the same 15 rows
    a, b = await live.first_pages([gateway, hub])
    assert b.progress.same_as == gateway.key and b.bids == []
    await live.rest([gateway, hub], {gateway.key: a.progress, hub.key: b.progress})
    assert [k for k, p in site.served if p == 2] == [gateway.key]

async def test_a_fulltext_search_over_the_limit_is_skipped(gem, clock):
    # LiveSearcher(skip_over=15); 25 rows under fullText|department and under exact|Department of Revenue
    ft, ex = await live.first_pages([SearchSpec(kind="fullText", value="department"),
                                     SearchSpec(kind="exact", value="Department of Revenue")])
    assert ft.progress.skipped and ft.bids == [] and not ex.progress.skipped

async def test_no_data_found_is_an_empty_complete_search(gem, clock):
    (out,) = await live.first_pages([SearchSpec(kind="fullText", value="nothing")])
    assert out.progress == SearchProgress(num_found=0, pages_read=1, page_count=0) and out.bids == []

async def test_a_search_that_fails_midway_keeps_its_pages_and_the_run_goes_on(gem, clock):
    # page 2 of fullText|aml returns HTTP 500 after the client's retries; fullText|kyc is fine
    more = await live.rest([aml, kyc], progress)
    assert more[0].progress.failed and more[0].progress.pages_read == 1 and len(more[1].bids) == 15

async def test_a_cached_base_search_is_reused_within_an_hour(gem, clock, tmp_path):
    # cached_keys={"category|services_home_itse_hi12433188"} with 25 rows: first_pages reads page 1 only,
    # rest reads pages 2-3 and stores the search; a second LiveSearcher on the same cache returns it complete
    assert first_run_pages == [1] and outcome.progress.pages_read == outcome.progress.page_count and site.served == []

async def test_an_old_or_unreadable_cache_file_is_ignored(tmp_path):
    cache = SearchCache(tmp_path, now=lambda: T0)
    cache.put("k", 3, docs)
    assert SearchCache(tmp_path, now=lambda: T0 + timedelta(minutes=61)).get("k") is None
    (next(tmp_path.glob("*.json"))).write_text("{not json")
    assert SearchCache(tmp_path, now=lambda: T0).get("k") is None
```

- [x] **Step 3: Run the tests to verify they fail.**
  Run: `uv run --no-sync pytest tests/tools/test_live.py -q`
  Expected: errors, `ModuleNotFoundError: gem_agent.tools.gem.live`.

- [x] **Step 4: Implement `SearchCache` in `gem_agent/cache/search_cache.py`.**
  - One file per key, named `sha1(key)[:16].json`, holding `{"key", "at", "num_found", "docs"}`.
  - Writes go to a temporary file in the same directory, then `os.replace`.
  - `get` returns `None` if the file is missing, fails to parse, has a different `key`, or `now() - at > ttl`.

- [x] **Step 5: Implement `LiveSearcher` in `gem_agent/tools/gem/live.py`.**
  - **Routing:** `category` goes through `search_bids(CategorySearch(category=value))`; the other kinds through `list_all_bids(AllBidsQuery(search_text=value, search_type=kind))`.
  - **Page 1 signature:** `(num_found, tuple of page-1 b_ids)`, compared against the earlier specs in the same call. A match sets `same_as`.
  - **Skip:** applies only to `fullText` with `num_found > skip_over`.
  - **Cached keys:** a key in `cached_keys` is served complete from the cache when the cache holds it. Otherwise it is read in two stages like any other search, and stored once it is complete: in `first_pages` if it has one page, otherwise in `rest`. Stage 1 never reads a base search past page 1.
  - **Failures:** any `GemHttpError` or `httpx` error while reading a page stops that spec with `failed=True` and keeps the pages already read. It is logged at WARNING.
  - **Duplicates:** bids are de-duplicated by `b_id` within a spec.

- [x] **Step 6: Run the tests to verify they pass.**
  Run: `uv run --no-sync pytest tests/tools/test_live.py -q`
  Expected: 7 passed.

- [x] **Step 7: Commit.**

```bash
git add gem_agent/tools/gem/live.py gem_agent/cache/search_cache.py tests/tools/test_live.py tests/tools/live_helpers.py
git commit -m "Live GeM searches in two stages, with repeats read once and a 1-hour base-search cache"
```

### Task 2: Bid cache writes only what changed, prunes, and is migrated

**Files:**
- Modify: `gem_agent/index/db.py` (`upsert` at lines 109–141; add `prune`)
- Create: `gem_agent/index/migrate.py`
- Modify: `gem_agent/config.py` (add `bid_cache_path`)
- Test: `tests/index/test_db.py`, `tests/index/test_migrate.py`

**Interfaces:**
- Produces:
  - `BidIndex.upsert(...)`: same signature and `UpsertCounts`. It now executes no SQL write for an unchanged row (same tracked fields and same `raw`).
  - `BidIndex.prune(*, closed_before: datetime) -> int`: deletes rows and their embeddings where `closing < closed_before`.
  - `migrate_bids(old: BidIndex, new: BidIndex, keep_ids: set[int]) -> int`: copies every row that has a non-NULL card (`''` included, since it marks a bid already tried) plus every row in `keep_ids`, with its raw listing and card. Returns the number copied.
  - `Settings.bid_cache_path -> Path`: `data_dir / "bids" / "bids.sqlite"`.

- [x] **Step 1: Write the failing tests.**

```python
def test_an_unchanged_bid_is_not_written_again(index):
    index.upsert([bid], seen_at=T1)
    before = index._conn.total_changes
    counts = index.upsert([bid], seen_at=T2)
    assert counts.unchanged == 1 and index._conn.total_changes == before

def test_prune_drops_bids_closed_more_than_30_days_ago(index):
    # closings T0 - 31 days, T0 - 29 days
    assert index.prune(closed_before=T0 - timedelta(days=30)) == 1 and index.get(old_id) is None

def test_migrate_copies_cards_tried_markers_and_labelled_bids_only(tmp_path):
    # old: 1 card "AML suite; AML", 2 card "", 3 no card, 4 no card; keep_ids={4}
    assert migrate_bids(old, new, {4}) == 3
    assert new.card(1) == "AML suite; AML" and new.card(2) == "" and new.get(3) is None and new.get(4) is not None
```

- [x] **Step 2: Run the tests to verify they fail.**
  Run: `uv run --no-sync pytest tests/index/test_db.py tests/index/test_migrate.py -q`
  Expected: `test_an_unchanged_bid…` fails (`total_changes` grows); the others error on missing names.

- [x] **Step 3: Implement.**
  - **`upsert`:** compare the old tracked tuple and the old `raw` first, and skip the `INSERT … ON CONFLICT` when both are equal.
  - **`prune`:** one `DELETE` per table inside the lock.
  - **`migrate_bids`:** `new.upsert([b], seen_at=site_now())`, then `new.set_card(b_id, card)` when the card is not None.
  - **`bid_cache_path`:** a property next to `index_path`.

- [x] **Step 4: Run the tests to verify they pass.**
  Run: `uv run --no-sync pytest tests/index -q`
  Expected: all pass. `test_sync.py` still exists at this point and must still pass.

- [x] **Step 5: Commit.**

```bash
git add gem_agent/index/db.py gem_agent/index/migrate.py gem_agent/config.py tests/index/test_db.py tests/index/test_migrate.py
git commit -m "Bid cache: write only new or changed rows, prune 30 days after closing, migrate cards"
```

### Task 3: Cards from a given list of bids

**Files:**
- Modify: `gem_agent/index/cards.py:159-190` (`write_cards`)
- Test: `tests/index/test_cards.py`

**Interfaces:**
- Produces: `async write_cards(index: BidIndex, collector, model: Model, *, bids: list[Bid] | None = None, limit: int = 40, text_chars: int = 12000, max_attachments: int = 3) -> CardReport`.
  - When `bids` is given, it writes cards for those of them that are vague titles with no card (`index.card(b_id) is None`), at most `limit`.
  - When `bids` is None, it behaves as today, using `index.needing_cards(limit)`.

- [x] **Step 1: Write the failing test.**

```python
async def test_cards_are_written_only_for_the_given_vague_bids_without_a_card(tmp_path):
    # index holds vague bids 1, 2 (2 already has card "x") and vague bid 3; bids=[bid1, bid2, non_vague_bid]
    report = await write_cards(index, collector, llm.model(), bids=[b1, b2, plain])
    assert report.written == 1 and collector.calls == [(1, True)] and index.card(3) is None
```

- [x] **Step 2: Run the test to verify it fails.**
  Run: `uv run --no-sync pytest tests/index/test_cards.py -q -k given_vague`
  Expected: FAIL, `TypeError: unexpected keyword argument 'bids'`.

- [x] **Step 3: Implement the `bids` parameter.**
  Filter the list with `is_vague_title` and `index.card(...) is None`, then keep the existing loop body.

- [x] **Step 4: Run the card tests.**
  Run: `uv run --no-sync pytest tests/index/test_cards.py -q`
  Expected: all pass.

- [x] **Step 5: Commit.**

```bash
git add gem_agent/index/cards.py tests/index/test_cards.py
git commit -m "Card writer can take the bids a run collected"
```

### Task 4: Live meaning check and the broad-class rule in the Chat step

**Files:**
- Create: `gem_agent/agents/probe.py`
- Modify: `gem_agent/agents/chat.py` (the `probe` type at line 127, the call at line 163, `UNDERSTAND_INSTRUCTIONS`)
- Test: `tests/agents/test_probe.py`, `tests/agents/test_chat.py` (make `FakeProbe.__call__` async), `tests/live/test_chat_live.py`

**Interfaces:**
- Consumes: `LiveSearcher.first_pages`, `SearchSpec` (Task 1).
- Produces:
  - `async probe_live(live: LiveSearcher, topic: TopicDefinition, window: ResolvedWindow, *, k: int = 8, max_terms: int = 3) -> ProbeResult`:
    - `strong`: the sum of `num_found` over the distinct non-skipped searches for the first `max_terms` entries of `[*topic.match_terms, *topic.acronyms]`.
    - `top_titles`: the first `k` titles across those searches, published within the window if it is a published window.
    - `window_count`: the `num_found` of an unfiltered `AllBidsQuery()` page 1, which gives the open bids in total.
  - `ChatAgent(model, *, probe: Callable[[TopicDefinition, ResolvedWindow], Awaitable[ProbeResult]], …)`.

- [x] **Step 1: Write the failing tests.**

```python
async def test_probe_counts_bids_matching_the_first_three_terms(gem, clock):
    # fullText|AML 12 rows, fullText|anti money laundering 0, fullText|transaction monitoring 2, 4th term never sent
    p = await probe_live(live, AML_TOPIC, ResolvedWindow())
    assert p.strong == 14 and len(p.top_titles) <= 8 and ("fullText|KYC", 1) not in site.served

def test_understand_instructions_keep_a_general_class_whole():
    assert "general class" in UNDERSTAND_INSTRUCTIONS and "never narrow" in UNDERSTAND_INSTRUCTIONS
```

  The live test `tests/live/test_chat_live.py::test_payment_systems_stays_a_broad_class` is marked `@pytest.mark.live`. It runs `ChatAgent.understand("t", "Payment systems tenders", …)` against the configured model and asserts:
  - the first topic's `match_terms` contain at least 3 of `{"UPI", "RTGS", "NEFT", "payment switch", "card management", "payment gateway"}`, matched case-insensitively as substrings
  - `"gateway"` is not the only product named in `meaning`

- [x] **Step 2: Run the tests to verify they fail.**
  Run: `uv run --no-sync pytest tests/agents/test_probe.py tests/agents/test_chat.py -q`
  Expected: errors on the missing module. The chat tests fail once `FakeProbe` is async.

- [x] **Step 3: Implement.**
  - **`probe_live`:** as specified in the Interfaces block above.
  - **The Chat step:** `probes = [await self.probe(t, window) for t, _ in candidates]`.
  - **The instruction:** add one line to `UNDERSTAND_INSTRUCTIONS`, after the acronym rule:
    "When the request names a general class of product or service (payment systems, treasury, core banking), the candidate is the whole class: describe it broadly and list its specific products in match_terms; never narrow it to one product."

- [x] **Step 4: Run the tests to verify they pass.**
  Run: `uv run --no-sync pytest tests/agents -q`
  Expected: all pass.
  Then run `uv run --no-sync pytest tests/live/test_chat_live.py -m live -q`. Expected: PASS. If it fails, report the model's topic to the user rather than loosening the assertion.

- [x] **Step 5: Commit.**

```bash
git add gem_agent/agents/probe.py gem_agent/agents/chat.py tests/agents/test_probe.py tests/agents/test_chat.py tests/live/test_chat_live.py
git commit -m "Chat checks meanings with live searches and keeps a general class whole"
```

### Task 5: Live Searcher

**Files:**
- Rewrite: `gem_agent/agents/searcher.py`
- Rewrite: `tests/agents/test_searcher.py`

**Interfaces:**
- Consumes: `LiveSearcher`, `SearchSpec`, `SearchProgress`, `SearchOutcome` (Task 1); `BidIndex` used as the bid cache, named `bid_cache` (Task 2).
- Produces:
  - `BASE_SPECS: list[SearchSpec]`, in this order (spec 6.2):
    - `SearchSpec(kind="category", value="services_home_itse_hi12433188")`
    - `SearchSpec(kind="exact", value="Department of Financial Services")`
    - `SearchSpec(kind="exact", value="BANKING SECTOR")`
    - `SearchSpec(kind="fullText", value="consultants")`
  - `search_specs(plan: SearchPlan) -> list[SearchSpec]`: `BASE_SPECS`, then `category` for each of `plan.categories`, then `exact` for each of `[*plan.ministries, *plan.departments, *plan.prefer_ministries, *plan.prefer_departments]`, then `fullText` for each of `plan.terms`. Duplicates are removed and the order is kept.
  - `Searcher(model: Model, bid_cache: BidIndex, live: LiveSearcher, *, settings: SearchSettings | None = None)`.
  - `async Searcher.search(topic, plan, *, stage: Literal["first", "rest"] = "first", progress: dict[str, SearchProgress] | None = None, exclude: set[int] = frozenset()) -> SearchResult`.
  - `SearchResult(candidates: list[Candidate], pool: int, over_cap: bool = False, progress: dict[str, SearchProgress] = {})`, where `progress` is new.
  - `SearchSettings`: keep `screen_batch`, `screen_max_tokens` and `cap`. Remove `semantic_pool`, `word_pool` and `category_pages`.

- [x] **Step 1: Write the failing tests.** They use `FakeGem` + `FakeSearchSite` (Task 1), `ScriptedLLM`, `BidIndex(":memory:")`, and `plan()` / `keep()` as today.

```python
async def test_base_searches_run_even_when_the_plan_has_no_categories_or_buyers(gem, clock):
    await searcher.search(LOS, plan(terms=["LOS"], categories=[]))
    assert {k for k, _ in site.served} >= {s.key for s in BASE_SPECS}

async def test_first_stage_reads_page_one_and_rest_reads_the_remaining_pages(gem, clock):
    first = await searcher.search(LOS, p, stage="first")
    assert {pg for _, pg in site.served} == {1}
    await searcher.search(LOS, p, stage="rest", progress=first.progress, exclude={c.bid.b_id for c in first.candidates})
    assert 2 in {pg for k, pg in site.served if k == "fullText|loan origination"}

async def test_the_second_stage_returns_only_bids_the_first_did_not(gem, clock):
    assert not ({c.bid.b_id for c in second.candidates} & {c.bid.b_id for c in first.candidates})

async def test_vague_titles_without_a_card_are_never_dropped(gem, clock):
    # IT category serves vague bid 5; the screen keeps nothing
    assert 5 in ids(await searcher.search(LOS, p))

async def test_a_vague_title_with_a_card_is_screened_by_its_card(gem, clock):
    bid_cache.upsert([vague5], seen_at=T); bid_cache.set_card(5, "Loan origination system for RRBs; LOS")
    found = await searcher.search(LOS, p)
    assert "what it buys: Loan origination system for RRBs" in screened(llm)

async def test_the_window_is_applied_locally(gem, clock):
    # bid published 30 Oct, window published_from 2 Nov
    assert old_id not in ids(await searcher.search(LOS, plan(published_from=at(2, 0))))

async def test_kept_candidates_are_stored_in_the_bid_cache(gem, clock):
    found = await searcher.search(LOS, p)
    assert all(bid_cache.get(c.bid.b_id) is not None for c in found.candidates)

async def test_a_failed_title_screen_keeps_every_title(gem, clock):
    ...  # existing behaviour: screen raises, every title in the pool becomes a candidate with the "screen failed" reason
```

- [x] **Step 2: Run the tests to verify they fail.**
  Run: `uv run --no-sync pytest tests/agents/test_searcher.py -q`
  Expected: errors, `cannot import name 'BASE_SPECS'`.

- [x] **Step 3: Implement the Searcher.**
  1. **Run the searches.** `stage == "first"` calls `live.first_pages(search_specs(plan))`; `"rest"` calls `live.rest(search_specs(plan), progress)`.
  2. **Pool.** Merge the bids by `b_id`, dropping those in `exclude`, and record `why = [spec.key …]`.
  3. **Window.** Apply the plan's published and closing window locally, using the filter in today's `_category_bids`.
  4. **Screen.** Look up cards from `bid_cache.card(b_id)` for vague titles, then screen with the existing `_screen_titles`. Vague titles without a card are kept with the reason "vague title: only its documents can tell".
  5. **Cache.** `bid_cache.upsert(kept, seen_at=site_now())`.
  6. **Sort and cap.** Sort and apply the cap as today.
  7. **Return.** `SearchResult(…, progress={o.spec.key: o.progress for o in outcomes})`. In the `rest` stage, `progress` merges into the given one.

  Keep `SCREEN_INSTRUCTIONS`, `ScreenOutput`, `Candidate` and the screen code unchanged.

- [x] **Step 4: Run the tests to verify they pass.**
  Run: `uv run --no-sync pytest tests/agents/test_searcher.py -q`
  Expected: all pass.

- [x] **Step 5: Commit.**

```bash
git add gem_agent/agents/searcher.py tests/agents/test_searcher.py
git commit -m "Searcher searches GeM live: base searches, two stages, bid cache"
```

### Task 6: Run graph — judge after page 1, then search deeper; cards after the run

**Files:**
- Modify: `gem_agent/graph/state.py`, `gem_agent/graph/build.py:70-160`, `gem_agent/graph/services.py`
- Test: `tests/graph/test_run_graph.py` and `tests/graph/test_checkpoint.py`, with fakes in `tests/graph/fakes.py`

**Interfaces:**
- Consumes: `Searcher.search(..., stage=, progress=, exclude=)`, `SearchProgress` (Task 5, Task 1); `write_cards(..., bids=)` (Task 3).
- Produces:
  - **New `RunState` fields:**
    - `stage: Literal["first", "rest", "done"] = "first"`
    - `search_progress: dict[str, dict[str, SearchProgress]] = {}`, keyed by `topic.key()`, then by spec key
  - **New `Services` field:** `cards: Callable[[list[Bid]], Awaitable[Any]] | None = None`.
  - **Graph:**
    1. `plan → search → (retry | process)`
    2. `process → (search_rest | report)`: `search_rest` when `stage == "first"`, else `report`
    3. `search_rest → process`
    4. `report → END`
  - **`search_rest`:**
    - Calls `search(..., stage="rest")` for each topic.
    - Appends the new candidates to `state.candidates`, without repeating any already there.
    - Sets `stage="rest"`.
    - Emits the progress line `"Searching deeper: {n} more bids to read…"` when `n > 0`.
  - **The second `process`** sets `stage="done"`.
  - **`report`:** calls `s.cards([o.bid for o in outcomes if o.files and is_vague_title(o.bid.title)])` when `s.cards` is set. Card failures are logged and never fail the run.

- [x] **Step 1: Write the failing tests.**

```python
async def test_a_run_judges_page_one_bids_then_searches_deeper_and_judges_the_new_ones(...):
    # fake searcher: stage first -> bids 1, 2; stage rest -> bids 2, 3
    assert fake_searcher.stages == ["first", "rest"] and judged_ids == [1, 2, 3]

async def test_a_resumed_run_continues_with_the_second_stage(...):
    # checkpoint after the first process node (stage="first", done_ids=[1, 2]); resume
    assert fake_searcher.stages == ["rest"] and judged_ids_after_resume == [3]

async def test_cards_are_written_for_vague_bids_collected_in_the_run(...):
    assert card_calls == [[vague_bid]]

async def test_a_card_failure_does_not_fail_the_run(...):
    # s.cards raises RuntimeError
    assert final_message.startswith("Done:")
```

- [x] **Step 2: Run the tests to verify they fail.**
  Run: `uv run --no-sync pytest tests/graph -q`
  Expected: the new tests fail. Existing graph tests may need the fake searcher to accept `stage`, `progress` and `exclude`; update the fakes.

- [x] **Step 3: Implement the graph change as specified in the Interfaces block.**
  - `after_search` returns `retry` only in the first stage.
  - The retry, widen and narrow behaviour is otherwise unchanged.

- [x] **Step 4: Run the tests to verify they pass.**
  Run: `uv run --no-sync pytest tests/graph -q`
  Expected: all pass.

- [x] **Step 5: Commit.**

```bash
git add gem_agent/graph tests/graph
git commit -m "Runs judge the first page's bids, then search deeper; cards written after a run"
```

### Task 7: Wiring, migration command, retire the index and timers

**Files:**
- Modify: `gem_agent/app/factory.py`, `gem_agent/cache/lookups.py`, `gem_agent/cli.py`, `gem_agent/eval/run.py`
- Delete: `gem_agent/index/sync.py`, `gem_agent/index/query.py`, `gem_agent/index/probe.py`, `tests/index/test_sync.py`, `tests/index/test_query.py`, `tests/index/test_probe.py`, `deploy/systemd/` (all files)
- Modify: `README.md`, `OVERVIEW.md` (remove the index and timer rows and steps; describe live search in one paragraph), `docs/SPEC-GEM_TENDER_AGENT.md` (mark 6.2 as built)
- Test: `tests/test_cli.py`, `tests/app/` (factory smoke test, if one exists)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - **`build_services`:**
    - `bid_cache = BidIndex(settings.bid_cache_path)`
    - `live = LiveSearcher(session, cache=SearchCache(settings.cache_dir / "searches"), cached_keys=frozenset(s.key for s in BASE_SPECS))`
    - `ChatAgent(probe=functools.partial(probe_live, live))`
    - `Searcher(model, bid_cache, live)`
    - `Services(cards=lambda found: write_cards(bid_cache, collector, model, bids=found))`
    - The startup hooks also run `bid_cache.prune(closed_before=site_now() - timedelta(days=30))`.
    - No bge-m3 is loaded for search. The Judge keeps its embedder choice unchanged.
  - **`LookupCatalog(cache_dir, bid_cache: BidIndex, …)`:** the same behaviour, reading distinct ministries and departments from the bid cache, with the site's ministry list as fallback.
  - **CLI:** `gem-agent bids migrate [--from PATH]`, defaulting to `settings.index_path`. It runs `migrate_bids` with `keep_ids` = every `b_id` in `eval/labelled_set.json` and prints the count. `gem-agent index …` is removed.
  - **Eval:** `judge_set(..., bid_cache: BidIndex, ...)` uses `bid_cache.get`, so it reads the bid cache.
  - **Logging:** in `cli.main`, after `logging.basicConfig`, set `logging.getLogger("httpx").setLevel(logging.WARNING)`.

- [x] **Step 1: Write the failing tests.**

```python
def test_bids_migrate_copies_cards_from_the_old_index(tmp_path, monkeypatch):
    # old index at tmp/old.sqlite with one card; run main(["bids", "migrate", "--from", str(old)])
    assert BidIndex(settings.bid_cache_path).card(1) == "AML suite; AML"

def test_the_index_command_is_gone():
    with pytest.raises(SystemExit):
        main(["index", "sync"])

def test_httpx_logs_at_warning_after_setup():
    setup_logging(quiet=False)
    assert logging.getLogger("httpx").level == logging.WARNING
```

  For the last test, factor the two `logging.basicConfig` calls in `cli.py` into `setup_logging(quiet: bool) -> None` and test that helper directly.

- [x] **Step 2: Run the tests to verify they fail.**
  Run: `uv run --no-sync pytest tests/test_cli.py -q`
  Expected: the new tests fail.

- [x] **Step 3: Implement the wiring.**
  - Make the factory, catalog, CLI and eval changes listed above.
  - In `gem_agent/agents/planner.py`, replace the first line of `PLAN_INSTRUCTIONS` ("The main search is by meaning over all open tender titles, so filters are optional extras:") with:
    "Every term runs as a live GeM title search, and GeM matches mainly on a term's first word, so give terms whose first word is specific (\"UPI\", \"RTGS\", \"Murex\"), not \"digital …\" or \"online …\". Base searches for bank IT and consultancy bids always run as well, so these are extras:"
  - Delete the retired files.
  - Update `README.md` and `OVERVIEW.md`: remove the timer install instructions, the "Ops: systemd timers" row and the background-index part of the flow diagram, and add a short "live search" paragraph that points to spec 6.2.
  - In the spec's 6.2 heading, change "not built yet" to "built <date>", and the matching notes in 5.2, 5.3 and 15. The spec is local only, since `docs/` is git-ignored.

- [x] **Step 4: Run the full offline suite.**
  Run: `uv run --no-sync pytest -m "not live" -q`
  Expected: all pass, with no test importing `gem_agent.index.sync`, `query` or `probe` (`grep -rn "index.sync\|index.query\|index.probe" gem_agent tests` prints nothing).

- [x] **Step 5: Migrate and smoke-test against the real site,** with the user present. This sends live requests to GeM.

  Run `uv run --no-sync gem-agent bids migrate`. Expected: about 250 or more cards copied, plus the labelled bids.

  Then `uv run --no-sync gem-agent chat "AML tenders published in the last 2 weeks"`. Expected:
  - the first result within about 1 minute
  - "Searching deeper" appears
  - the IT Projects category bids 10069360, 10142481 and 10143892 are reached if still open (labelled relevant, spec 13)
  - `/proc/pressure/io` stays near its idle level during the run

  Check the Mazagon Dock case: `gem-agent chat "M&A advisory tenders"`. The candidates should include 9939422, if still open, through `fullText|consultants`.

  Report the timings to the user.

- [x] **Step 6: Commit.**

```bash
git add -A gem_agent tests README.md OVERVIEW.md deploy
git commit -m "Wire live search; replace the index command with bids migrate; retire the index sync and timers"
```

- [x] **Step 7: Hand the old data to the user.**
  Tell the user the old index and its timers can now be removed by hand, and give the commands. Do not run them.
  - `rm data/index/bids.sqlite data/index/bids.sqlite-wal data/index/bids.sqlite-shm`
  - `systemctl --user disable --now gem-index-sync.timer gem-index-refresh.timer`
  - `rm ~/.config/systemd/user/gem-index-*.{service,timer}`
