# Describe Once, Match, Safety Net — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Describe each bid once into the bid cache, let the model match descriptions to each topic (A), and keep today's title screen plus triage as a safety net, so fewer relevant tenders are missed.

**Architecture:** The Searcher now also returns the in-window bids its title screen dropped (`rest_pool`). The `process` node describes every pool bid that has no current description (one queue per request, capped), matches descriptions per topic in batches of 40, routes each bid with one pure function (`graph/deciding.py: route`), then fully reads bids in the order match → unsure → safety net. Bids dropped by both checks get an outcome with a `not_read` reason and land in Quarantine. A harness (`gem-agent eval match`) replays A and today's path on the labelled set.

**Tech Stack:** Python 3, FastAPI, langgraph, pydantic-ai `NativeOutput` against Qwen on vLLM, SQLite (`data/bids/bids.sqlite`), React 19 + Vite, pytest with `ScriptedLLM`/`FunctionModel` fakes.

**Spec:** `docs/superpowers/specs/2026-11-24-describe-once-design.md`. Plan 1 (`docs/superpowers/plans/2026-11-24-repairs.md`) is already merged on `main`, so every name below is real code as of commit `1313c5e`.

## Global Constraints

- **Anti-goal:** no path drops a bid because only one check said no. While `match_safety_net` is on, a bid is dropped only when A says "no" **and** today's path says no.
- `MATCH_SAFETY_NET=true` by default (`Settings.match_safety_net: bool = True`). Only a person flips it; no code flips it.
- New descriptions per request: capped at **300**, one queue across the request's topics. Full reads per topic: capped at **150** (`SearchSettings.cap`), across both stages.
- Describing runs at most **3** model calls at a time. GeM traffic stays serialised through the shared rate limiter; describing adds no GeM parallelism.
- No new periodic or background job. Describing happens only inside a search, or by hand with `gem-agent describe --refresh` (cached documents only, no GeM requests).
- Model-facing schemas keep every field required (no defaults on `MatchOut`). Model calls use `temperature: 0.0`.
- Progress lines follow plan 1's rules: plain words, no "triage", "placeholder" or internal budgets; "1 bid" singular; a finished step has no trailing "…".
- Shared spinning disk: check `cat /proc/pressure/io` before the full test suite or any live run, and hold off above ~20%. One live search at a time.
- Commits: `GIT_AUTHOR_NAME=rjdiips GIT_AUTHOR_EMAIL=rjdiips@gmail.com GIT_COMMITTER_NAME=rjdiips GIT_COMMITTER_EMAIL=rjdiips@gmail.com git commit -m "..."`. No `Co-Authored-By` lines (AGENTS.md). `git add` named files only: the working tree holds unrelated prototype changes. Never commit anything under `data/runs`.
- Test command: `uv run pytest <path> -q`. Web: `cd web && npm test -- --run` and `npm run build`.

## Review Focus

1. **A run resumed after describing** must not describe or match again: descriptions are in SQLite, and bids with outcomes are in `done_ids`. Test in Task 8.
2. **A description with an empty subject** (the documents did not say what is bought) cannot be matched; the bid must take today's path, never be dropped. Test in Task 6.
3. **The model answering with bad numbers** (out of range, or a number in both `match` and `unsure`): unknown numbers are ignored and a bid named in both is a match. Test in Task 5.
4. **Restoring a bid that was never read** must move it to Relevant and keep the description reason visible. Test in Task 7.
5. **A bid whose bid document fails to download while being described** is an error outcome ("could not be read"), as plan 1 made it, not an undescribed bid silently left on today's path. Test in Task 8.

---

### Task 1: The description store

**Files:**
- Modify: `gem_agent/index/db.py` (schema, `Description`, store methods, card copy, prune)
- Test: `tests/index/test_db.py`

**Interfaces:**
- Produces, in `gem_agent/index/db.py`:
  ```python
  class Description(BaseModel):
      b_id: int
      doc_hash: str              # agents.judge.doc_hash of the files it was written from; "" for a copied card
      subject: str               # "" when the documents did not say what is bought
      terms: list[str]
      sources: list[str]         # file names read
      has_scope: bool            # a scope-of-work section or an RFP attachment was read
      buyer_org: str | None
      model: str
      prompt_version: str        # "card" for a copied card
      written_at: datetime
      def text(self) -> str      # "subject; term1; term2", "" when subject is ""
      def usable(self) -> bool   # subject != ""
      def current(self, model: str) -> bool  # prompt_version != "card" and self.model == model
  ```
  `BidIndex.description(b_id) -> Description | None`, `BidIndex.descriptions(b_ids: Iterable[int]) -> dict[int, Description]`, `BidIndex.put_description(d: Description) -> None` (insert or replace), `BidIndex.descriptions_to_refresh(model: str, prompt_version: str, limit: int) -> list[Bid]` (open bids whose row has another model or prompt version, closing soonest first).

- [ ] **Step 1: Write the failing tests** in `tests/index/test_db.py`:

```python
def test_a_description_round_trips(tmp_path): ...
    # put_description(d) then description(d.b_id) == d; descriptions([d.b_id, 999]) == {d.b_id: d}

def test_description_text_usable_and_current():
    d = Description(b_id=1, doc_hash="h", subject="Enterprise KYC and AML for NABARD", terms=["AML", "CKYC"],
                    sources=["GeM-Bidding.pdf"], has_scope=True, buyer_org="NABARD", model="m", prompt_version="1",
                    written_at=NOW)
    assert d.text() == "Enterprise KYC and AML for NABARD; AML; CKYC" and d.usable() and d.current("m")
    assert not d.current("other") and not d.model_copy(update={"prompt_version": "card"}).current("m")
    assert d.model_copy(update={"subject": ""}).text() == "" and not d.model_copy(update={"subject": ""}).usable()

def test_cards_are_copied_in_as_stale_descriptions_once(tmp_path):
    # an index with set_card(1, "AML suite; AML") and set_card(2, ""), closed and reopened twice:
    d = index.description(1)
    assert (d.subject, d.terms, d.prompt_version, d.doc_hash, d.has_scope) == ("AML suite", ["AML"], "card", "", False)
    assert index.description(2) is None            # a tried-and-empty card is not copied
    # after put_description(fresh row for 1) and reopening, the copy does not overwrite it

def test_pruning_a_bid_deletes_its_description(tmp_path): ...
    # prune(closed_before=...) removes the bid and its description; a kept (labelled) bid keeps its row

def test_descriptions_to_refresh_lists_open_bids_on_another_model_or_prompt(tmp_path): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/index/test_db.py -q`
Expected: FAIL with `ImportError: cannot import name 'Description'`.

- [ ] **Step 3: Implement in `gem_agent/index/db.py`.** Add a `descriptions` table to `SCHEMA` (columns as the model; `terms` and `sources` as JSON text, `has_scope` as INTEGER, `b_id` PRIMARY KEY). The card copy runs in `__init__` only when the table did not exist before `executescript` (check `sqlite_master` first): each non-empty `card` splits on `"; "` into subject and terms. `prune` deletes `descriptions` rows for the same doomed b_ids before deleting the bids.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/index -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/index/db.py tests/index/test_db.py
git commit -m "Bid cache keeps one description per bid; existing cards copied in as stale rows"
```

---

### Task 2: The describer replaces the card writer

**Files:**
- Create: `gem_agent/index/describe.py`
- Modify: `gem_agent/index/cards.py` (keep the text helpers, remove the writer), `gem_agent/graph/build.py` (report no longer writes cards), `gem_agent/graph/services.py`, `gem_agent/app/factory.py`, `gem_agent/cli.py`
- Test: `tests/index/test_describe.py` (new), `tests/index/test_cards.py` (drop writer tests), `tests/graph/test_run_graph.py` (drop the three card tests and the `cards=` argument)

**Interfaces:**
- Consumes: `Description`, `BidIndex.put_description`, `BidIndex.description`, `BidIndex.descriptions_to_refresh` (Task 1); `agents.judge.doc_hash(files) -> str`.
- Produces, in `gem_agent/index/describe.py`:
  ```python
  DESCRIBE_PROMPT_VERSION = "1"
  DESCRIBE_INSTRUCTIONS: str   # the spike's DESCRIBE prompt, verbatim from eval/describe_ab/run.py
  class Describer:
      def __init__(self, model: Model, cache: DocCache, index: BidIndex, *, model_name: str, parallel: int = 3)
      model_name: str
      async def describe(self, bid: Bid, files: BidFiles) -> Description | None   # stored; None on failure, nothing stored
      def known(self, b_ids: Iterable[int]) -> dict[int, Description]             # index.descriptions
      def stale(self, b_id: int, files: BidFiles) -> bool   # a row exists and its doc_hash != doc_hash(files)
  @dataclass
  class RefreshReport: described: int = 0; no_documents: int = 0; failed: int = 0
  async def refresh_descriptions(describer: Describer, index: BidIndex, cache: DocCache, *, limit: int) -> RefreshReport
  ```
  In `gem_agent/index/cards.py`: `rfp_sections(collector, files, bid_text, max_files=3) -> tuple[str, list[str]]` (text, attachment file names used); `_rfp_sections` stays as a wrapper returning only the text (eval scripts import it).
  `Services` loses `cards`, gains `describer: Any = None` (None: the run behaves as today, Task 8).

- [ ] **Step 1: Write the failing tests** in `tests/index/test_describe.py`, using `bid_files` and `ScriptedLLM` from `tests/agents/helpers.py`:

```python
async def test_a_description_is_written_from_the_bid_document_and_scope_and_stored(tmp_path):
    # files: GeM-Bidding.pdf (bid_document) and RFP.pdf (attachment, a page with a "3. Scope of Work" heading line)
    d = await describer.describe(bid, files)
    assert d.subject == "Enterprise KYC and AML for NABARD" and d.has_scope
    assert d.sources == ["GeM-Bidding.pdf", "RFP.pdf"] and d.doc_hash == doc_hash(files)
    assert (d.model, d.prompt_version) == ("scripted", "1") and index.description(bid.b_id) == d
    assert "Scope of Work" in llm.calls[0][1]

async def test_a_failed_description_is_not_stored(tmp_path):
    # the script answers with text that is not the schema: describe returns None, index.description is None

async def test_a_description_restating_the_title_is_stored_empty(tmp_path):
    # subject "Hiring of Agency for IT Projects" for that title: stored with subject "" and terms [], usable() False

async def test_stale_only_when_a_row_exists_and_the_documents_changed(tmp_path):
    # no row: False; row from these files: False; files with another sha: True; a copied card (doc_hash ""): True

async def test_refresh_reads_only_cached_documents(tmp_path):
    # two open bids with "card" rows, one with documents in the DocCache: described 1, no_documents 1; no collector used
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/index/test_describe.py -q`
Expected: FAIL with `ModuleNotFoundError: gem_agent.index.describe`.

- [ ] **Step 3: Implement `Describer` in `gem_agent/index/describe.py`.** The prompt is built as the spike's `describe_all` builds it (title, buyer, bid and parent documents up to 12000 chars, then `rfp_sections`); output type `CardOutput`; `max_tokens` 400; `retries=2`. A subject for which `cards.is_generic(subject, title)` holds is stored as `""` with no terms. `parallel` is an `asyncio.Semaphore` around the model call only.

- [ ] **Step 4: Remove the card writer.** Delete `write_cards`, `CARD_INSTRUCTIONS`, `CardReport` and `clear_generic_cards` from `cards.py` with their tests; delete `write_cards` and its call from `build.py` `report`; drop `Services.cards`; in `factory.build_services` set `describer=Describer(model, cache, bid_cache, model_name=settings.llm_model)`.

- [ ] **Step 5: Add `gem-agent describe --refresh [--limit 200]`** in `cli.py`. It builds a `Describer` and `DocCache` only (no session), calls `refresh_descriptions`, and prints `described {n}, {m} without cached documents, {k} failed`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/index tests/graph tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gem_agent/index/describe.py gem_agent/index/cards.py gem_agent/graph/build.py gem_agent/graph/services.py \
        gem_agent/app/factory.py gem_agent/cli.py tests/index/test_describe.py tests/index/test_cards.py \
        tests/graph/test_run_graph.py
git commit -m "Describer writes one description per bid; the end-of-run card writer is gone"
```

---

### Task 3: The Searcher returns the whole in-window pool

**Files:**
- Modify: `gem_agent/agents/searcher.py`
- Modify: `tests/graph/fakes.py` (`FakeSearcher` gains a `dropped` argument)
- Test: `tests/agents/test_searcher.py`

**Interfaces:**
- Consumes: `BidIndex.description` (Task 1).
- Produces: `SearchResult.rest_pool: list[Candidate] = []`, the in-window bids not in `candidates`. Each one's `why` starts with `"title screen: no"` (the screen dropped it) or `"over the cap"` (kept, but cut by the cap). `candidates`, `cut` and `over_cap` keep their meaning, so the narrowing retry is unchanged. `FakeSearcher(results, rest=..., dropped=((),))`: per stage-1 call, bids returned in `rest_pool` with `why=["title screen: no", "fake"]`.

- [ ] **Step 1: Write the failing tests** in `tests/agents/test_searcher.py`:

```python
async def test_titles_the_screen_drops_come_back_in_the_rest_pool(gem, clock, bid_cache):
    # 3 in-window bids, the screen keeps the first: candidates [1], rest_pool [2, 3], each why[0] == "title screen: no"

async def test_titles_the_cap_cuts_come_back_in_the_rest_pool(gem, clock, bid_cache):
    # cap=1, two kept: cut == 1 and the cut bid is in rest_pool with why[0] == "over the cap"

async def test_a_vague_title_with_a_description_is_screened_by_it(gem, clock, bid_cache):
    # replaces the card test: put_description(subject "Lockers for branches") for a vague title:
    # the screen prompt shows "what it buys: Lockers for branches"; the bid is screened, not kept unseen

async def test_a_vague_title_with_an_empty_description_is_never_dropped(gem, clock, bid_cache): ...

async def test_every_in_window_bid_is_stored_in_the_bid_cache(gem, clock, bid_cache):
    # rest_pool bids too: bid_cache.get(b) is not None for every pool bid
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_searcher.py -q`
Expected: FAIL (`SearchResult` has no `rest_pool`).

- [ ] **Step 3: Implement in `Searcher.search`.** The vague-title context comes from `bid_cache.description(b_id)` when `usable()`, shown as `text()`; the `card` column is no longer read. Upsert every pool bid, not only candidates. Update the module docstring's step 4.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_searcher.py tests/graph -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/agents/searcher.py tests/agents/test_searcher.py tests/graph/fakes.py
git commit -m "Searcher returns the bids its title screen dropped, for the description check"
```

---

### Task 4: Deeper pages stop at the window and share the budget

**Files:**
- Modify: `gem_agent/tools/gem/live.py` (`LiveSearcher.rest`, `_rest`), `gem_agent/agents/searcher.py` (passes the window)
- Test: `tests/tools/test_live.py`

**Interfaces:**
- Produces: `LiveSearcher.rest(specs, progress, *, budget=None, on_page=None, window_start: datetime | None = None, keep: Callable[[Bid], bool] | None = None)`.
  - `budget` counts only distinct bids for which `keep(bid)` is true (all bids when `keep` is None).
  - Each pending search gets `remaining budget // pending searches left` (at least 1) when it starts, so what one search leaves unused rolls on to the next.
  - An all-bids search (`kind` `exact` or `fullText`, sorted newest published first) stops after a page whose bids were all published before `window_start`. A `category` search is not stopped this way.
  - The Searcher calls it with `window_start=plan.published_from, keep=lambda b: self._in_scope(b, plan)`.

- [ ] **Step 1: Write the failing tests** in `tests/tools/test_live.py` (rows built with `row(b_id, published, closing)`):

```python
async def test_an_all_bids_search_stops_after_a_page_older_than_the_window(gem, clock):
    # 30 rows: 10 published on T0, 20 on T0 - 30 days; window_start T0 - 1 day:
    # site.served == [(key, 1), (key, 2)]

async def test_a_category_search_is_not_stopped_by_dates(gem, clock):
    # same rows under a category key: all 3 pages served

async def test_the_budget_counts_only_bids_in_the_window(gem, clock):
    # budget 10, keep = published >= T0, page 2 all outside: page 3 is still read

async def test_each_search_gets_an_equal_share_of_the_budget(gem, clock):
    # two fullText searches of 30 rows each, budget 20: each serves pages 1-2 and neither serves page 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/tools/test_live.py -q`
Expected: FAIL (`rest()` got an unexpected keyword argument `window_start`).

- [ ] **Step 3: Implement in `live.py`; pass the window from `Searcher.search` stage `"rest"`.** A search stopped early stays `pending` and is never cached as complete (plan 1's rule).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/tools/test_live.py tests/agents/test_searcher.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/tools/gem/live.py gem_agent/agents/searcher.py tests/tools/test_live.py
git commit -m "Deeper pages stop past the window start, count only in-window bids and share the budget"
```

---

### Task 5: The matcher (A)

**Files:**
- Create: `gem_agent/agents/matcher.py`
- Test: `tests/agents/test_matcher.py`

**Interfaces:**
- Produces:
  ```python
  MATCH_PROMPT_VERSION = "1"
  MATCH_INSTRUCTIONS: str   # the spike's MATCH prompt, plus: "Each line ends with the buyer. A topic about a buyer
                            # (a ministry, department or bank) matches on the buyer as well as on what is bought."
  Call = Literal["match", "unsure", "no"]
  class MatchItem(BaseModel): b_id: int; description: str; buyer: str
  class MatchOut(BaseModel): match: list[int]; unsure: list[int]       # both required, numbers only
  class Matcher:
      def __init__(self, model: Model, *, batch: int = 40, max_tokens: int = 600)
      calls: int                                                       # model calls made so far
      async def match(self, topic: TopicDefinition, items: list[MatchItem]) -> dict[int, Call]
  def buyer_line(bid: Bid, buyer_org: str | None) -> str   # ministry / department / buyer_org, empty parts left out
  ```
  Each line sent is `[i] {description} | buyer: {buyer}`. A batch that fails is split in half and each half tried once; bids in a half that fails again are `"unsure"`.

- [ ] **Step 1: Write the failing tests** in `tests/agents/test_matcher.py` (`ScriptedLLM` keyed by `MATCH_INSTRUCTIONS`, topic `LOS`):

```python
async def test_each_bid_gets_match_unsure_or_no():
    # reply {"match": [0], "unsure": [2]} for 3 items: {1: "match", 2: "no", 3: "unsure"}

async def test_a_number_in_both_lists_is_a_match_and_unknown_numbers_are_ignored():
    # reply {"match": [0, 7], "unsure": [0, -1]} for 2 items: {1: "match", 2: "no"}

async def test_more_than_forty_are_sent_in_batches_of_forty():
    # 85 items: matcher.calls == 3, every b_id answered

async def test_a_failing_batch_is_split_and_a_half_that_fails_again_is_unsure():
    # 4 items; replies: bad, {"match": [0, 1], "unsure": []}, bad: {1: "match", 2: "match", 3: "unsure", 4: "unsure"}

async def test_the_buyer_is_on_each_line():
    assert "| buyer: Ministry of Finance / Department of Financial Services / NABARD" in llm.calls[0][1]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_matcher.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `Matcher`** with `Agent(model, output_type=NativeOutput(MatchOut), instructions=MATCH_INSTRUCTIONS, retries=2)` and `{"max_tokens": 600, "temperature": 0.0}`; the prompt is `f"{topic.render()}\n\nTenders:\n{listing}"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_matcher.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/agents/matcher.py tests/agents/test_matcher.py
git commit -m "Matcher decides match, unsure or no from descriptions, in batches of 40"
```

---

### Task 6: Routing each bid, in one pure function

**Files:**
- Create: `gem_agent/graph/deciding.py`
- Test: `tests/graph/test_deciding.py`

**Interfaces:**
- Consumes: `Candidate`, `from_own_search` (searcher), `Description` (Task 1), `Call` (Task 5).
- Produces:
  ```python
  Route = Literal["match", "unsure", "net", "today", "drop", "unseen"]
  def screen_kept(c: Candidate) -> bool        # False only when c.why[0] == "title screen: no"
  def route(c: Candidate, description: Description | None, call: Call | None, *, safety_net: bool) -> Route
  def describe_order(cands: list[Candidate], similarity: dict[int, float]) -> list[Candidate]
  def not_read_reason(description: Description | None, checks: list[str]) -> str
  async def title_similarity(embedder: Embedder | None, topic: TopicDefinition, titles: list[str]) -> list[float]
  ```
  `route` rules, first that applies:
  1. No description, `not description.usable()`, or `call is None` → `"today"` if `screen_kept(c)` else `"unseen"` (today's path: a title the screen dropped gets no outcome, as today).
  2. `call == "match"` → `"match"`; `call == "unsure"` → `"unsure"`.
  3. `call == "no"`: if `not safety_net and description.has_scope` → `"drop"`; elif `screen_kept(c)` → `"net"` (triage decides); else → `"drop"`.

  `describe_order` key: own-search hits first (`from_own_search`), then screen-kept, then closing date (day; no date last), then higher similarity, then b_id.
  `not_read_reason(d, ["the description check", "the title check"])` → `"Not a match: buys Lockers for branches. Ruled out by the description check and the title check."`; with no usable description, `"Not a match. Ruled out by …"`.
  `title_similarity` runs `embedder.encode` in `asyncio.to_thread` on `topic.meaning + "; " + "; ".join(topic.match_terms)` and the titles, and returns cosine scores; `[0.0, …]` when the embedder is None.

- [ ] **Step 1: Write the failing tests** in `tests/graph/test_deciding.py`:

```python
def test_a_bid_is_never_dropped_on_one_no_while_the_net_is_on():
    # every combination of screen kept/dropped, has_scope True/False, call match/unsure/no/None:
    # with safety_net=True, route == "drop" only when call == "no" and not screen_kept

def test_with_the_net_off_a_no_with_scope_drops_and_one_without_takes_todays_path(): ...

def test_an_empty_description_takes_todays_path():           # Review Focus 2
    assert route(kept, desc(subject=""), "no", safety_net=False) == "today"
    assert route(dropped, desc(subject=""), "no", safety_net=False) == "unseen"

def test_undescribed_bids_take_todays_path_even_with_the_net_off(): ...

def test_describe_order_own_search_then_kept_then_closing_then_similarity(): ...

def test_not_read_reason_names_the_subject_and_the_checks(): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/graph/test_deciding.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `gem_agent/graph/deciding.py`.**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/graph/test_deciding.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/graph/deciding.py tests/graph/test_deciding.py
git commit -m "One routing rule: a bid is dropped only when the description and the title check both say no"
```

---

### Task 7: Outcomes for bids not read, and why a bid was read

**Files:**
- Modify: `gem_agent/graph/store.py` (`Outcome`), `gem_agent/graph/views.py`, `gem_agent/graph/build.py` (`running_count`, `report` counts)
- Test: `tests/graph/test_views_state.py`, `tests/graph/test_store.py`, `tests/graph/test_run_graph.py`

**Interfaces:**
- Produces: `Outcome.read_because: Literal["match", "unsure", "net"] | None = None` and `Outcome.not_read: str = ""` (the `not_read_reason` text; set only for bids not fully read). `bid_result` gains `"read_because"`; its `"reason"` is, first that applies: the "doesn't count" ruling, `not_read`, the verdict's reason, the error text. `_judged(o)` is true for `o.not_read`. `running_count` counts a `not_read` bid as "not a match". `report` counts states over outcomes with a verdict **or** `not_read`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_a_bid_not_read_is_quarantined_with_its_reason():
    o = Outcome(b_id=1, group="LOS", bid=bid(1), not_read="Not a match: buys Lockers. Ruled out by the description check and the title check.")
    r = bid_result("r", o)
    assert r["state"] == "quarantined" and r["reason"].startswith("Not a match: buys Lockers") and r["read_because"] is None

def test_restoring_a_bid_not_read_makes_it_relevant_and_keeps_the_reason():   # Review Focus 4
    r = bid_result("r", o.model_copy(update={"restored": True}))
    assert r["state"] == "relevant" and r["reason"].startswith("Not a match: buys Lockers")

def test_a_read_bid_says_why_it_was_read(): ...   # read_because "net" comes through

def test_running_count_counts_bids_not_read_as_not_a_match():
    assert running_count(1, 1, [o]) == "Read 1 of 1 bids: 0 relevant, 1 not a match."

def test_outcomes_stored_before_these_fields_still_load(tmp_path): ...   # an outcomes.jsonl line without them
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/graph -q`
Expected: FAIL (`Outcome` has no field `not_read`).

- [ ] **Step 3: Implement the fields and the view changes.**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/graph -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/graph/store.py gem_agent/graph/views.py gem_agent/graph/build.py tests/graph
git commit -m "Bids ruled out without a full read are quarantined with the reason; read bids say why"
```

---

### Task 8: The run describes, matches, routes and reads

**Files:**
- Modify: `gem_agent/graph/build.py` (`search`, `search_rest`, `process`, new progress-line helpers), `gem_agent/graph/state.py`, `gem_agent/graph/services.py`, `gem_agent/config.py`, `gem_agent/app/factory.py`, `.env.example`
- Modify: `tests/graph/fakes.py` (`FakeDescriber`, `FakeMatcher`)
- Test: `tests/graph/test_run_graph.py`

**Interfaces:**
- Consumes: Tasks 1–7: `Describer.describe/known/stale/model_name`, `Matcher.match`, `MatchItem`, `buyer_line`, `route`, `describe_order`, `title_similarity`, `not_read_reason`, `SearchResult.rest_pool`, `Outcome.read_because/not_read`.
- Produces:
  - `Settings.match_safety_net: bool = True`, `Settings.describe_cap: int = 300`.
  - `Services` gains `matcher: Any = None`, `safety_net: bool = True`, `describe_cap: int = 300`, `similarity_embedder: Any = None`. With `describer` or `matcher` None, `process` routes every candidate `"today"`: the run is exactly today's, so the existing run-graph tests stay as they are.
  - `RunState` gains `pool_rest: list[Candidate] = []`, `described: int = 0` (new descriptions this request), `reads: dict[str, int] = {}` (full reads per group, both stages).
  - In `build.py`: `described_line(new: int, known: int) -> str` → `"Described 12 new bids (40 already known)."`; `matched_line(n: int, match: int, might: int) -> str` → `"Matched 52 bids: 6 match, 4 might."`; `undescribed_line(n: int) -> str` → `"3 bids not yet described; they will be checked on the next search."` (all singular for 1).
  - `FakeDescriber(subjects: dict[int, str] | None = None, *, fail=(), has_scope=True)`: default subject `"Loan Origination System for RRBs"`; `described: list[int]`; `model_name = "fake"`; `known()` returns what it described; `stale()` is always False. `FakeMatcher(calls: dict[int, Call] | None = None, default="match")`: `batches: list[list[int]]`.

  `process`, in order:
  1. `todo` = candidates then `pool_rest`, minus `done_ids`.
  2. **Describe**: those without a current description (`known`, `current(describer.model_name)`), in `describe_order`, at most `describe_cap - state.described`. Each: `collect(bid)` with attachments, then `describe`. A collect error is an error outcome (as plan 1), saved and done. A failed description leaves the bid undescribed. One `described_line`; an `undescribed_line` when the cap left bids out.
  3. **Match** per group, in batches flushed at 40 ready descriptions or when describing is done (bids already described go first). One `matched_line`.
  4. **Route** each bid. `"drop"` → outcome with `not_read`, checks `["the description check", "the title check"]` (or only the first when the net is off). `"unseen"` → nothing.
  5. **Net and today**: `collect(bid, attachments=False)` then `judge.screen`. A clear no: for `"today"` the triage verdict outcome as now; for `"net"` the same plus `not_read` with checks `["the description check", "the first-pages check"]`.
  6. **Read**: groups match → unsure → net/today, closing soonest within each, at most `cap - reads[group]` per group; the rest are counted into `cut` and reported with `cut_line` at the end. Each read: `collect(bid)`, re-describe when `describer.stale(...)`, `judge.judge(topic, bid, files, triage=<the triage answer or None>)`, `settle`, `read_because` = route (`"today"` → None).
  `search` stores `result.rest_pool` (merged across topics, excluding candidates) in `pool_rest` and stops printing `cut_line` (process reports cuts now); `search_rest` excludes `pool_rest` ids too and appends its own `rest_pool`.

- [ ] **Step 1: Write the failing tests** in `tests/graph/test_run_graph.py` (services built with `describer=FakeDescriber(...)`, `matcher=FakeMatcher(...)`):

```python
async def test_every_pool_bid_is_described_once_and_matched(tmp_path):
    # candidates [1, 2], dropped [3]: describer.described == [1, 2, 3] in describe order; one matcher batch of 3

async def test_a_bid_the_description_matches_is_read_even_when_the_title_screen_dropped_it(tmp_path):
    # dropped [3], matcher {3: "match"}: judge.judged contains 3 and its result has read_because "match"

async def test_a_no_from_both_checks_quarantines_without_reading(tmp_path):
    # dropped [3], matcher {3: "no"}: 3 not in judge.judged; result state "quarantined", reason starts "Not a match: buys"

async def test_the_safety_net_reads_a_no_the_title_screen_kept(tmp_path):
    # candidates [1], matcher {1: "no"}, FakeJudge maybe: read with read_because "net"

async def test_a_net_bid_that_triage_rules_out_is_quarantined_with_both_checks(tmp_path): ...

async def test_with_the_net_off_a_no_with_scope_drops_without_triage(tmp_path): ...

async def test_reads_go_match_then_unsure_then_net(tmp_path):
    # workers=1; judge.judged == [match bid, unsure bid, net bid] regardless of closing dates across groups

async def test_new_descriptions_are_capped_per_request_and_the_rest_take_todays_path(tmp_path):
    # describe_cap=2, candidates [1, 2, 3]: 2 described; bid 3 triaged then read with read_because None;
    # progress has "1 bid not yet described; they will be checked on the next search."

async def test_the_read_cap_counts_across_both_stages_and_reports_what_it_cuts(tmp_path): ...

async def test_a_bid_whose_documents_fail_while_described_is_an_error(tmp_path):   # Review Focus 5
    # FakeCollector(fail=[1]) raising DocumentUnavailable: outcome error, not described, not triaged

async def test_a_resumed_run_does_not_describe_or_match_again(tmp_path):         # Review Focus 1
    # as test_a_crashed_run_resumes_from_its_checkpoint: describer.described and matcher batches have no repeats

def test_describe_and_match_lines():
    assert described_line(1, 0) == "Described 1 new bid (0 already known)."
    assert matched_line(52, 6, 4) == "Matched 52 bids: 6 match, 4 might."
    assert undescribed_line(3) == "3 bids not yet described; they will be checked on the next search."
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/graph/test_run_graph.py -q`
Expected: the new tests FAIL; the existing ones still PASS.

- [ ] **Step 3: Implement** settings, services, state, fakes and the `process` steps above. In `factory.build_services` set `matcher=Matcher(model)`, `safety_net=settings.match_safety_net`, `describe_cap=settings.describe_cap`, `similarity_embedder=judge_embedder(settings)`. Add `MATCH_SAFETY_NET=true` to `.env.example`. Update the `build.py` module docstring to the new process steps.

- [ ] **Step 4: Run the tests to verify they pass** (check `/proc/pressure/io` first)

Run: `uv run pytest -q`
Expected: PASS, whole suite.

- [ ] **Step 5: Commit**

```bash
git add gem_agent/graph/build.py gem_agent/graph/state.py gem_agent/graph/services.py gem_agent/config.py \
        gem_agent/app/factory.py .env.example tests/graph/fakes.py tests/graph/test_run_graph.py
git commit -m "Runs describe every pool bid once, match descriptions, and keep the title check as a safety net"
```

---

### Task 9: UI shows why a bid was read

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/components/ResultsPanel.tsx`, `web/src/api/mock.ts`, `web/src/test/fakeApi.ts`
- Test: `web/src/components/ResultsPanel.test.tsx`

**Interfaces:**
- Consumes: `bid_result["read_because"]` (Task 7): `"match" | "unsure" | "net" | null`.
- Produces: under a read bid's verdict, one line: `Read because: matches` / `Read because: might match` / `Read because: kept by the title check`; nothing for `null`. Quarantine rows already show `reason`, which now carries the description and the checks.

- [ ] **Step 1: Write the failing test** in `ResultsPanel.test.tsx`:

```tsx
it('says why a bid was read', () => {
  render(<ResultsPanel ... bids={[bid({ b_id: 7, read_because: 'net' })]} />)
  expect(screen.getByText('Read because: kept by the title check')).toBeInTheDocument()
})
it('says nothing when the bid took the old path', () => { /* read_because: null → no "Read because" text */ })
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- --run src/components/ResultsPanel.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Add `read_because` to the `BidResult` type and render the line in `ResultsPanel.tsx`** in the `gt-why` block's style.

- [ ] **Step 4: Run tests and build**

Run: `cd web && npm test -- --run && npm run build`
Expected: PASS and a clean build.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/types.ts web/src/components/ResultsPanel.tsx web/src/components/ResultsPanel.test.tsx \
        web/src/api/mock.ts web/src/test/fakeApi.ts
git commit -m "Results say why each bid was read"
```

---

### Task 10: The tuning harness, `gem-agent eval match`

**Files:**
- Create: `gem_agent/eval/match.py`
- Modify: `gem_agent/cli.py` (`eval` takes an optional `what`: `judge` (default) or `match`, and `--repeats 3`)
- Delete: `eval/describe_ab/` (untracked spike; `rm -r`, nothing to commit)
- Test: `tests/eval/test_match.py`

**Interfaces:**
- Consumes: `Describer`, `Matcher`, `MatchItem`, `buyer_line`, `route`, `Searcher._screen_titles`, `Judge.screen`, `LabelledSet`.
- Produces:
  ```python
  BUYER_TOPICS = frozenset({"mof"})   # topics a description cannot decide alone: scored apart
  def checked(label: Label) -> bool    # not (label.to_review and not label.reviewed_by)
  class WayScore(BaseModel):
      way: Literal["today", "A", "today or A"]; topics: Literal["subject", "buyer"]; run: int
      checked_missed: int; all_missed: int; negatives_read: int; negatives: int; model_calls: int
      missed: list[str]            # "topic:b_id: description or title"
  class MatchReport(BaseModel): scores: list[WayScore]; retire: bool
  async def describe_labelled(labelled: LabelledSet, describer: Describer, index: BidIndex, cache: DocCache) -> dict[int, Description]
  def score_way(way, reads: dict[tuple[int, str], bool], labelled: LabelledSet, *, topics, run, model_calls, descriptions) -> WayScore
  async def evaluate(labelled, *, describer, matcher, searcher, judge, index, cache, repeats: int = 3, topic: str | None = None) -> MatchReport
  def report_text(report: MatchReport) -> str
  ```
  - `describe_labelled` reuses current rows and describes the rest from `cache.load` only (no collector, no GeM). A bid with no cached documents stays undescribed. A bid the bid cache lost is built from its label, as the spike's `get_bid` does.
  - Per run: today = title screen (descriptions only for vague titles), then `judge.screen` with a temporary `VerdictCache`. A = `route(..., safety_net=False)`. "Today or A" = `route(..., safety_net=True)`. A bid routed `"net"` or `"today"` counts as read when today's path reads it.
  - `retire` is true when A misses no checked label in every one of the `repeats` runs, subject topics only. `report_text` ends with either `"A alone missed no checked label in 3 of 3 runs: the safety net can be retired (MATCH_SAFETY_NET=false). A person decides."` or `"Keep the safety net: A alone missed checked labels in {k} of {n} runs."`.

- [ ] **Step 1: Write the failing tests** in `tests/eval/test_match.py`:

```python
def test_score_way_counts_checked_and_all_misses_and_non_matches_read(): ...
def test_buyer_topics_are_scored_apart(): ...
def test_retire_only_when_a_misses_no_checked_label_in_every_run(): ...
async def test_describe_labelled_reads_only_cached_documents(tmp_path):
    # a labelled bid without cached documents stays undescribed; a FakeCollector passed nowhere is never called
def test_report_text_lists_missed_tenders_with_their_descriptions(): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/eval/test_match.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `gem_agent/eval/match.py` and the CLI wiring.**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/eval tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Run it once against the real model** (no GeM traffic; check `/proc/pressure/io` first; vLLM is shared, so run it when nobody else is mid-batch)

Run: `uv run gem-agent eval match`
Expected: a report per way and run. "Today" and "today or A" checked-label misses are in the spike's range (today about 4, today or A about 1). Then `rm -r eval/describe_ab`.

- [ ] **Step 6: Commit**

```bash
git add gem_agent/eval/match.py gem_agent/cli.py tests/eval/test_match.py
git commit -m "gem-agent eval match scores A, today's path and both on the labelled set, three runs each"
```

---

### Task 11: First tuning round of the match prompt

**Files:**
- Modify: `gem_agent/agents/matcher.py` (`MATCH_INSTRUCTIONS`, `MATCH_PROMPT_VERSION`)
- Create: `eval/match_tuning.md` (one table row per prompt version tried)

**Interfaces:**
- Consumes: `gem-agent eval match` (Task 10).

- [ ] **Step 1: Record the baseline.** Run `uv run gem-agent eval match` on prompt version "1". In `eval/match_tuning.md`, add a row with A's checked misses (worst of 3 runs), all misses (worst of 3), non-matches read, and "today or A" checked misses.

- [ ] **Step 2: Change the prompt for one miss at a time**, each as its own version ("2", "3", "4"):
  - card-management or issuing software for a *payments* topic: "match" when the topic's own systems are what is bought;
  - a named module inside a broader suite (EFRM in a "risk and compliance suite"): "match" when the module is named, "unsure" when only the suite is;
  - a product in the same family as the topic: "unsure", never "no".

- [ ] **Step 3: Score each version** with `uv run gem-agent eval match`. Keep a change only if A's worst-run checked misses and all misses are no higher than the version before; otherwise revert it. Record every version's row, kept or not.

- [ ] **Step 4: Run the matcher tests**

Run: `uv run pytest tests/agents/test_matcher.py -q`
Expected: PASS (the tests key on `MATCH_INSTRUCTIONS` by name, not its text).

- [ ] **Step 5: Commit**

```bash
git add gem_agent/agents/matcher.py eval/match_tuning.md
git commit -m "Match prompt, first tuning round: <kept versions> (A checked misses <before> -> <after>)"
```

---

### Task 12: Docs

**Files:**
- Modify: `docs/APP_FLOW.md`, `docs/OVERVIEW.md`

- [ ] **Step 1: Rewrite the process sections** to describe: the whole in-window pool; describe once (stored in `bids.sqlite`, 300 new per request, reused by every later run and user); match per topic; the safety net and the `MATCH_SAFETY_NET` setting; the read order and the 150 cap; Quarantine reasons for bids not read; `gem-agent describe --refresh`; `gem-agent eval match` and when the net may be retired. Remove every mention of bid cards being written after a run.

- [ ] **Step 2: Check no stale names remain**

Run: `grep -n "write_cards\|card writer" docs/APP_FLOW.md docs/OVERVIEW.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/APP_FLOW.md docs/OVERVIEW.md
git commit -m "Docs: describe once, match, safety net"
```
