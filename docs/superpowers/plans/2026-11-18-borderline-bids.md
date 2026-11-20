# Borderline Bids Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When whether a bid counts depends on the user, the Judge marks it borderline and asks a question. The user rules on it with one click. The ruling is saved as a memory fact for that topic, and it settles similar bids in the same run and in later runs.

**Architecture:** The Judge stays the same for every user. Its verdict becomes three-way, so the shared verdict cache is unchanged in shape. A small per-user rulings agent applies the user's ruling facts after judging, inside the graph's `process` node and from a new rule endpoint. A bid's state (relevant / borderline / quarantined) is computed in one place, `views.bid_state`, and the zip, the counts and the UI all read it.

**Tech Stack:** Python 3, pydantic / pydantic-ai (`NativeOutput`), langgraph, FastAPI, pytest (asyncio auto); React + TypeScript, vitest.

**Spec:** [docs/superpowers/specs/2026-11-18-borderline-bids-design.md](../specs/2026-11-18-borderline-bids-design.md), which builds on [docs/GEM_TENDER_AGENT.md](../../GEM_TENDER_AGENT.md).

## Progress (keep updated: resume from here)

- **Where the work happens:** git worktree `/apps/rajdeep_workspace/gem_agent/.claude/worktrees/borderline-bids`, branch `worktree-borderline-bids`, base `70a9e9c`. The detailed ledger (rulings, deferred minors, agent ids) is `.superpowers/sdd/2026-11-18-borderline-bids/progress.md` inside that worktree.
- **Task 1:** ✅ done, `c19a016`, reviewed clean.
- **Task 2:** ✅ done, `c0f1ffe` and `94fec91` (fix: ruling facts kept away from the memory step), reviewed clean.
- **Task 3:** ✅ done, `f2421da`, reviewed clean.
- **Task 4:** ✅ done, `98ffcf7` and `68dec5b` (fix: backfill writes through `update_outcomes`), reviewed clean.
- **Task 5:** ✅ done, `ac9e54d`, reviewed clean (minor test gaps logged in the ledger).
- **Task 6:** ✅ done, `bca6a2f` and `4c47eea` (fix: re-ruling a bid is a no-op when the decision is the same; a replaced fact re-settles the bids it had settled), reviewed clean. Note: all Task 6 tests are in `tests/app/test_api.py` (ruled acceptable).
- **Task 7:** ✅ done, `c4613e2`, reviewed clean.
- **Task 8:** ✅ code done, `88727df` and `4c2c1a8` (fix: the eval prints the borderline rate and the unsure agreement), reviewed clean. Docs edited in the main checkout's `docs/GEM_TENDER_AGENT.md` (4.1, 5.4, 5.5, 13) and `docs/APP_FLOW.md` (new step 7). These are git-ignored, so not committed. Still open: the manual browser check and the live `gem-agent eval --topic ai`.
- **Final whole-branch review:** done at `4c2c1a8`, verdict "with fixes". Four Important issues: two rule clicks on the same bid at once leave an orphan fact; no pending or error UI; `mergeChanged` has no run guard; the Memory tab is stale and editing a removed fact revives it. Cheap minors are included too.
- **Final fix wave:** ✅ done, `e32dda3` (rulings serialised per run and re-checked at write; UI pending and error state; run guard on merge; memory refreshes after a ruling; a removed fact can't be edited back; a blank fact gives 502; a contradicted fact is replaced directly; doc 4.1 counter wording). Re-review clean. Suites at `e32dda3`: pytest 412 passed, vitest 51 passed, build ok.
- **Remaining (needs the user):** (1) merge or PR of branch `worktree-borderline-bids`; (2) manual browser check (Task 8 step 5); (3) live `uv run gem-agent eval --topic ai` (paid model); (4) the spec 6.3 re-judge step for verdicts cached before this change; (5) parked minors listed at the end of the ledger.
- **Full ledger** (every ruling and deferred minor): [2026-11-18-borderline-bids.ledger.md](2026-11-18-borderline-bids.ledger.md), copied from the worktree's `.superpowers/sdd/` workspace.

**Commits (outdated note, kept for history):** `/apps/rajdeep_workspace/gem_agent` is not a git repository. Each task ends with a checkpoint (the full suite passes) instead of a commit. If git is set up first, commit at each checkpoint.

## Global Constraints

- Every field of a model-facing schema is required: no defaults on the output models given to `NativeOutput`. Add each new output model to `test_every_model_facing_schema_requires_all_its_fields` in `tests/agents/test_judge.py`.
- Free text the model writes has a `max_length`: question ≤ 200, fact ≤ 200, reason ≤ 200. Add the new fields to `test_free_text_in_model_answers_is_bounded`.
- `Verdict.relevant` is true only for the decision "relevant". Borderline bids are never in the zip, the relevant count, or the run list's `found` until the user rules "counts".
- The Judge never sees user memory. Rulings live on the run's `Outcome`, never in `VerdictCache`.
- Ruling facts are matched to bids by topic short name (`agents.chat.short_name(topic)`), not by `topic.key()`.
- Ruling facts are left out of `MemoryStore.prompt_facts` and don't count toward its limit of 20.
- No model call is made while the run's outcomes lock is held.
- Old stored verdicts and outcomes (no `borderline`, `question`, `ruling` or `undone` fields) still load.
- Run Python tests with `uv run pytest -q` from the project root, and web tests with `cd web && npx vitest run`.

## Review Focus

1. **A verdict cached before this change** (JSON without `borderline` / `question`) must load as a yes/no verdict that is not borderline. Test in Task 1.
2. **The model says borderline but leaves the question empty.** The verdict must still show the user something to decide, so the question falls back to the reason. Test in Task 1.
3. **A rule request for a bid that isn't borderline** (it's relevant, quarantined, or errored) must be refused with 409, and the bid must not change. Test in Task 6.
4. **A rule or undo request on another user's run** must return 404 and change nothing. Test in Task 6.
5. **The model fails while writing the fact.** The request returns 502, and there is no new fact, no ruling and no change to other bids. Test in Task 6.

---

### Task 1: Judge gives a three-way decision

**Files:**
- Modify: `gem_agent/agents/schemas.py` (`JudgeOutput`, `Verdict`)
- Modify: `gem_agent/agents/judge.py` (`JUDGE_INSTRUCTIONS`, `Judge.judge`, `Judge._decide`)
- Test: `tests/agents/test_judge.py`

**Interfaces:**
- Produces:
  - `schemas.Decision = Literal["relevant", "borderline", "not_relevant"]`
  - `JudgeOutput.decision: Decision`, which replaces `relevant: bool`
  - `JudgeOutput.question: str` with `max_length=200`, required
  - `Verdict.borderline: bool = False` and `Verdict.question: str = ""`

- [x] **Step 1: Write the failing tests** in `tests/agents/test_judge.py`. Use the file's existing `ScriptedLLM` and `bid_files` setup; copy how the existing relevant-verdict test scripts `JUDGE_INSTRUCTIONS`.

```python
async def test_borderline_verdict_keeps_the_question_and_is_not_relevant(...):
    # script JUDGE_INSTRUCTIONS → {"decision": "borderline", "question": "AI is one part of a digital twin. Does that count as an AI platform for you?", ...verified quote...}
    v = await judge.judge(TOPIC, bid, files)
    assert v.borderline is True and v.relevant is False
    assert v.question.startswith("AI is one part of a digital twin")

async def test_borderline_without_a_question_falls_back_to_the_reason(...):   # Review Focus 2
    # decision "borderline", question "", reason "Digital twin with an ML layer."
    assert v.borderline and v.question == "Digital twin with an ML layer."

async def test_relevant_and_not_relevant_have_no_question(...):
    # decision "relevant" with question "stray text" → v.relevant, not v.borderline, v.question == ""

def test_verdicts_cached_before_borderline_still_load():                       # Review Focus 1
    old = {...a Verdict dict without "borderline"/"question"...}
    v = Verdict.model_validate(old)
    assert v.borderline is False and v.question == ""
```

Also add `JudgeOutput.question` to `test_free_text_in_model_answers_is_bounded`, and update every existing script reply in this file from `"relevant": true/false` to `"decision": "relevant"/"not_relevant"` with `"question": ""`.

- [x] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/agents/test_judge.py -q`. Expected: the new tests FAIL (no field `decision`).

- [x] **Step 3: Implement.**
  - In `schemas.py`: make the field changes listed under Interfaces. In `question`'s description, say it is empty unless the decision is borderline.
  - In `Judge.judge`:
    - `relevant = out.decision == "relevant"`
    - `borderline = out.decision == "borderline"`
    - `question = (out.question.strip() or out.reason) if borderline else ""`
  - In `_decide`: the quote retry runs when `out.decision != "not_relevant"`, and `review` uses `out.decision == "relevant"`.
  - Add a bullet to `JUDGE_INSTRUCTIONS`, right after the "If the passages don't settle it" bullet:

    > Borderline: the documents show something close to the topic, and whether it counts depends on the buyer's purpose or the reader's line of business, not on missing evidence. Then ask, in one sentence, the specific question the reader must answer (e.g. "AI is one part of a digital twin platform. Does that count as an AI platform for you?"). Otherwise leave question empty.

  The deep read (confidence below `deep_below`) is unchanged and runs before a borderline verdict is stored.

- [x] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/agents -q`. Expected: PASS.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`. Expected: all pass. Run `grep -rn "\.relevant" gem_agent/agents` and check that no remaining use reads `JudgeOutput.relevant`.

---

### Task 2: Memory holds ruling facts by topic

**Files:**
- Modify: `gem_agent/agents/schemas.py` (`MemoryFact`)
- Modify: `gem_agent/memory/user_memory.py`
- Modify: `gem_agent/app/routes/memory.py` (`list_memory`)
- Test: `tests/memory/test_user_memory.py`, `tests/app/test_api.py`

**Interfaces:**
- Produces:
  - `MemoryFact.kind: Literal["general", "ruling"] = "general"` and `MemoryFact.topic: str = ""` (a topic short name)
  - `MemoryStore.add_ruling(user: str, fact: str, *, topic: str, run_id: str, today: date, evidence: str) -> MemoryFact`: source "stated", active at once, `strength=RUNS_TO_LEARN`
  - `MemoryStore.ruling_facts(user: str, topic: str) -> list[MemoryFact]`: active, not removed, kind "ruling", `topic` equal to the given topic
  - `MemoryStore.forget(user: str, fact_id: str, evidence: str | None = None) -> None`: existing method; `evidence`, when given, replaces the fact's evidence
  - `prompt_facts` leaves out kind "ruling"
  - `GET /api/memory` items gain `"kind"` and `"topic"`

- [x] **Step 1: Write the failing tests.**

```python
def test_a_ruling_is_active_at_once_and_tied_to_its_topic(tmp_path):
    m = MemoryStore(tmp_path)
    f = m.add_ruling("asha", "AI that is one part of a larger system counts as an AI platform",
                     topic="AI platform", run_id="r1", today=D, evidence="you ruled on GEM/2026/B/7914442 on 18 Nov")
    assert f.active and f.kind == "ruling" and f.source == "stated"
    assert m.ruling_facts("asha", "AI platform") == [f]
    assert m.ruling_facts("asha", "Anti-Money Laundering") == []
    assert m.ruling_facts("ravi", "AI platform") == []

def test_rulings_stay_out_of_the_chat_prompt_and_its_limit(tmp_path):
    m = MemoryStore(tmp_path)
    m.add_stated("asha", "We sell LOS software", run_id="r0", today=D)
    for i in range(25):
        m.add_ruling("asha", f"ruling {i}", topic="AI platform", run_id="r1", today=D, evidence="e")
    assert [f.fact for f in m.prompt_facts("asha")] == ["We sell LOS software"]

def test_forget_with_evidence_records_why(tmp_path):
    # add_ruling, then forget(..., evidence="replaced by your ruling on … on 18 Nov") → removed, evidence updated
```

In `tests/app/test_api.py::test_memory_list_edit_forget`, assert that the listed fact has `"kind": "general"` and `"topic": ""`.

- [x] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/memory tests/app/test_api.py -q`. Expected: FAIL (no `add_ruling`).

- [x] **Step 3: Implement** the fields and methods listed under Interfaces. `add_ruling` creates a new fact every time; don't reuse `same_fact` merging, because a ruling's identity is its id. `list_memory` adds the two fields.

- [x] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/memory tests/app/test_api.py -q`. Expected: PASS.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`. Expected: all pass.

---

### Task 3: The rulings agent

**Files:**
- Create: `gem_agent/agents/rulings.py`
- Test: `tests/agents/test_rulings.py`, and the schema lists in `tests/agents/test_judge.py`

**Interfaces:**
- Consumes: `MemoryFact` (Task 2), `TopicDefinition`
- Produces:
  - `class RulingMatch(BaseModel): fact_id: str; decision: Literal["counts", "not"]`
  - `class RulingsAgent(model: Model)` with two methods:
    - `async resolve(topic: TopicDefinition, question: str, reason: str, facts: list[MemoryFact]) -> RulingMatch | None`. It makes no model call when `facts` is empty.
    - `async write_fact(topic: TopicDefinition, question: str, decision: Literal["counts", "not"]) -> str`
  - Model-facing outputs:
    - `resolve_output_model(fact_ids: list[str]) -> type[BaseModel]`, built with `create_model` as `planner.plan_output_model` does: `fact_id: Literal[*fact_ids, "none"]`, `decision: Literal["counts", "not", "none"]`, `reason: str` (max 200)
    - `FactOut(fact: str = Field(max_length=200))`
  - `RESOLVE_INSTRUCTIONS` and `FACT_INSTRUCTIONS` constants, so tests can script them

- [x] **Step 1: Write the failing tests** in `tests/agents/test_rulings.py`, using `ScriptedLLM` from `tests/agents/helpers.py`.

```python
async def test_no_facts_means_no_call():
    llm = ScriptedLLM({}); agent = RulingsAgent(llm.model())
    assert await agent.resolve(TOPIC, "Does X count?", "r", []) is None and llm.calls == []

async def test_a_matching_fact_decides():
    # script RESOLVE_INSTRUCTIONS → {"fact_id": "f1", "decision": "counts", "reason": "same case"}
    assert await agent.resolve(TOPIC, q, r, [fact("f1")]) == RulingMatch(fact_id="f1", decision="counts")

async def test_none_means_undecided():
    # → {"fact_id": "none", "decision": "none", "reason": "different case"}  → None
    # also: {"fact_id": "f1", "decision": "none", ...} → None (a half answer decides nothing)

async def test_the_prompt_carries_topic_question_and_each_fact_with_its_id():
    # after resolve: the last prompt in llm.calls contains topic.meaning, the question, "f1" and the fact's text

async def test_write_fact_returns_the_general_fact():
    # script FACT_INSTRUCTIONS → {"fact": "AI that is one part of a larger system counts as an AI platform"}
```

Add `resolve_output_model(["f1"])` and `FactOut` to `test_every_model_facing_schema_requires_all_its_fields`, and `FactOut.fact` plus the resolve `reason` to `test_free_text_in_model_answers_is_bounded`.

- [x] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/agents/test_rulings.py -q`. Expected: FAIL (no module).

- [x] **Step 3: Implement `gem_agent/agents/rulings.py`.** Follow `planner.py`'s pattern: an `Agent` with `NativeOutput`, `temperature` 0, small `max_tokens` (resolve 120, fact 100).
  - `RESOLVE_INSTRUCTIONS`: decide whether one of the user's earlier rulings answers this question. Answer only when a ruling covers the same case; otherwise answer none.
  - `FACT_INSTRUCTIONS`: write the user's answer as one general sentence that covers similar tenders for this topic. Name the kind of case, not the tender.

- [x] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/agents -q`. Expected: PASS.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`. Expected: all pass.

---

### Task 4: Outcome rulings, a locked store, and one effective state

**Files:**
- Modify: `gem_agent/graph/store.py` (`Ruling`, `Outcome`, `RunStore.save_outcome`, new `RunStore.update_outcomes`)
- Modify: `gem_agent/graph/views.py` (`bid_state`, `bid_result`, `run_list_item`)
- Modify: `gem_agent/app/routes/downloads.py` (`zip_all`)
- Modify: `gem_agent/graph/build.py` (the `report` node's counts and text)
- Modify: `gem_agent/jobs/backfill_queries.py:49` (use `bid_state`)
- Modify: `tests/graph/fakes.py`: `FakeJudge(no=(), borderline=())`. Bids in `borderline` get a verdict with `borderline=True`, `relevant=False`, and `question=f"Does bid {b_id} count for you?"`.
- Test: `tests/graph/test_views_errors.py` (or a new `tests/graph/test_views_state.py`), `tests/graph/test_store.py` (new), `tests/app/test_api.py`

**Interfaces:**
- Produces:
  - `class Ruling(BaseModel)`: `decision: Literal["counts", "not"]`, `by: Literal["you", "earlier"]`, `fact_id: str`, `fact: str`, `created_fact: bool = False`, `at: datetime`
  - `Outcome.ruling: Ruling | None = None` and `Outcome.undone: bool = False` (the user undid a ruling on this bid; never settle it automatically again)
  - `RunStore.update_outcomes(run_id: str, fn: Callable[[dict[int, Outcome]], Iterable[int]]) -> dict[int, Outcome]`. It holds an exclusive `fcntl` lock on `runs/<run_id>/outcomes.lock`, re-reads, applies `fn` (which returns the changed ids), writes, and returns the changed outcomes. `save_outcome` takes the same lock.
  - `views.State = Literal["relevant", "borderline", "quarantined"]`
  - `views.bid_state(o: Outcome) -> State`. The first rule that applies wins:
    1. `o.restored` → relevant
    2. a ruling of "counts" → relevant
    3. a ruling of "not" → quarantined
    4. borderline with no ruling → borderline
    5. otherwise `o.verdict.relevant` decides (an error counts as quarantined)
  - `bid_result` changes:
    - adds `"state"`, `"question"` and `"ruling"` (`{"decision", "by", "fact"}` or `None`)
    - `"relevant"` becomes `state == "relevant"`
    - for a ruling of "not", `"reason"` becomes `"Your ruling: " + ruling.fact`
  - `report` text: `"Done: {r} relevant, {q} quarantined[, {f} could not be read]."`, plus `" 1 bid needs your call: see Borderline."` or `" {b} bids need your call: see Borderline."` when b > 0

- [x] **Step 1: Write the failing tests.**

```python
def test_bid_state_rules():
    # one row per rule above, built from Outcome + Verdict(borderline=..., relevant=...) + Ruling
    assert bid_state(borderline_unruled) == "borderline"
    assert bid_state(borderline_ruled_counts) == "relevant"
    assert bid_state(borderline_ruled_not) == "quarantined"
    assert bid_state(ruled_not_then_restored) == "relevant"
    assert bid_result("r", borderline_ruled_not)["reason"].startswith("Your ruling: ")

def test_update_outcomes_rereads_inside_the_lock(tmp_path):
    store = RunStore(tmp_path); store.save_outcome("r", o1)
    stale = store.outcomes("r")              # an endpoint read before its model calls
    store.save_outcome("r", o2)              # process saved another bid meanwhile
    store.update_outcomes("r", lambda all_: [set_ruling(all_[1])])
    assert set(store.outcomes("r")) == {1, 2}   # o2 not lost
```

In `tests/app/test_api.py::test_file_download_and_zip`, add a borderline unruled bid to the run and assert that its folder is not in the zip. Rule the bid "counts" through `update_outcomes`, and assert that the folder is now in the zip.

In `tests/graph/test_run_graph.py`, assert that a run with one borderline bid ends with `"Done: 1 relevant, 1 quarantined. 1 bid needs your call: see Borderline."` when it has one relevant bid, one triage rejection and one borderline bid.

- [x] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/graph tests/app -q`. Expected: the new tests FAIL.

- [x] **Step 3: Implement** everything listed under Interfaces. The zip, `run_list_item.found`, the report and the backfill job all call `bid_state`. Leave `running_count` as is.

- [x] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/graph tests/app -q`. Expected: PASS.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`. Expected: all pass.

---

### Task 5: The run applies earlier rulings as it judges

**Files:**
- Create: `gem_agent/graph/rulings.py`
- Modify: `gem_agent/graph/services.py` (`rulings: Any = None`)
- Modify: `gem_agent/graph/build.py` (`process.one`)
- Modify: `gem_agent/app/factory.py` (wire `RulingsAgent(model)`)
- Modify: `tests/graph/fakes.py` (new `FakeRulings`)
- Test: `tests/graph/test_run_graph.py`

**Interfaces:**
- Consumes: `RulingsAgent.resolve` (Task 3), `MemoryStore.ruling_facts` (Task 2), `Ruling` and `Outcome` (Task 4)
- Produces:
  - `graph.rulings.topic_for(request: TenderRequest, group: str) -> TopicDefinition`: the topic whose `short_name` equals `group`, else `request.topics[0]`. This is the same rule `process` uses today; move it here.
  - `async graph.rulings.settle(s: Services, user: str, topic: TopicDefinition, verdict: Verdict, *, facts: list[MemoryFact] | None = None) -> Ruling | None`. It returns None when `s.rulings` is None or the verdict isn't borderline. `facts` defaults to `s.memory.ruling_facts(user, short_name(topic))`, read at call time. The result is `Ruling(by="earlier", fact=<that fact's text>, ...)`.
  - `FakeRulings(answers: dict[str, RulingMatch])`, keyed by question, with the same `resolve` and `write_fact` signatures. `write_fact` returns `f"fact for {question} ({decision})"` and can be set to raise.

- [x] **Step 1: Write the failing tests** in `tests/graph/test_run_graph.py`.

```python
async def test_a_borderline_bid_waits_for_the_user(tmp_path):
    s = services(tmp_path, judge=FakeJudge(borderline={2}))
    events = await ask(RunManager(s), "LOS tenders")
    [b2] = [e["bid"] for e in events if e["type"] == "result" and e["bid"]["b_id"] == 2]
    assert b2["state"] == "borderline" and b2["question"]

async def test_an_earlier_ruling_on_the_same_topic_decides_it(tmp_path):
    s = services(tmp_path, judge=FakeJudge(borderline={2}), rulings=FakeRulings({Q: RulingMatch(fact_id=..., decision="counts")}))
    s.memory.add_ruling("asha", "…", topic="Loan Origination System", run_id="r0", today=NOW.date(), evidence="e")
    # → b2 state "relevant", ruling.by == "earlier"

async def test_a_ruling_on_another_topic_is_not_used(tmp_path):
    # add_ruling(topic="AI platform") → FakeRulings.resolve never called, b2 stays borderline

async def test_a_ruling_made_mid_run_reaches_bids_judged_after_it(tmp_path):
    # BlockingCollector-style: release bid 1 → add_ruling while bid 2 is blocked → release → bid 2 decided "earlier"
```

`services()` in this file gains `rulings=None`, passed to `Services(rulings=...)`.

- [x] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/graph/test_run_graph.py -q`. Expected: FAIL.

- [x] **Step 3: Implement.** In `process.one`, after the verdict and before `save_outcome`, add `outcome.ruling = await settle(s, state.user, topic, verdict)` inside the existing try. A failing resolve call logs a warning and leaves the bid borderline; it never fails the bid. Wire `rulings=RulingsAgent(model)` in `factory.py`.

- [x] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/graph -q`. Expected: PASS.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`. Expected: all pass.

---

### Task 6: Rule, undo and restore through the API

**Files:**
- Modify: `gem_agent/graph/manager.py` (`rule`, `unrule`, `restore`, `NotBorderline`, `RulingFailed`)
- Modify: `gem_agent/app/routes/runs.py`
- Test: `tests/app/test_api.py`, `tests/graph/test_run_graph.py`

**Interfaces:**
- Consumes: `settle` and `topic_for` (Task 5), `RulingsAgent` (Task 3), `MemoryStore.add_ruling`, `ruling_facts` and `forget` (Task 2), `RunStore.update_outcomes` and `bid_result` (Task 4)
- Produces:
  - `class NotBorderline(RuntimeError)` and `class RulingFailed(RuntimeError)`
  - `async RunManager.rule(user: str, run_id: str, b_id: int, decision: Literal["counts", "not"]) -> list[dict]`: the changed `bid_result`s
  - `async RunManager.unrule(user: str, run_id: str, b_id: int) -> list[dict]`
  - `async RunManager.restore(user: str, run_id: str, b_id: int) -> list[dict]`: now returns the changed bids
  - `POST /api/runs/{run_id}/rule/{b_id}` with body `{"decision": "counts" | "not"}` → 200 `{"changed": [...]}`; 404 for an unknown or another user's run or bid; 409 `NotBorderline`; 502 `RulingFailed`
  - `DELETE /api/runs/{run_id}/rule/{b_id}` → 200 `{"changed": [...]}`
  - `POST /api/runs/{run_id}/restore/{b_id}` → 200 `{"changed": [...]}` (was 204)
  - Signal events: `{"type": "signal", "kind": "ruled" | "unruled", "b_id", "decision"?, "at"}`

`rule` does the following, in order. All model calls happen before any write.

1. Load the outcome. Raise `NotBorderline` unless `o.verdict` is borderline.
2. Work out the topic with `topic_for(record.request, o.group)`. Let `existing = memory.ruling_facts(user, short_name(topic))`. Call `match = await s.rulings.resolve(topic, q, reason, existing)`.
3. Choose the fact:
   - If `match` has the same decision, reuse that fact (`created_fact=False`).
   - Otherwise, call `text = await s.rulings.write_fact(...)`. Any exception here becomes `RulingFailed`. Then `memory.add_ruling(...)` (`created_fact=True`). If `match` had the opposite decision, `memory.forget(match.fact_id, evidence="replaced by your ruling on {bid_number} on {d %d %b}")`.
4. Sweep: for every other outcome in the run that is borderline, unruled, not undone and in the same group, call `settle(..., facts=[new_fact])`, outside the lock.
5. In one `update_outcomes` call, set this bid's ruling (`by="you"`), and set each sweep result where that bid is still unruled and not undone. Append the signal event and return the changed `bid_result`s.

`unrule`:
- No ruling → return `[]`.
- A ruling by you with `created_fact` → forget the fact, and clear every ruling in the run with that `fact_id` and `by="earlier"`. Those bids go back to borderline; they are not marked undone.
- In every case, this bid gets `ruling=None` and `undone=True`.

`restore`: when the bid's ruling has decision "not", return `await self.rule(user, run_id, b_id, "counts")`. Otherwise keep today's behaviour and return `[bid_result(o)]`.

- [x] **Step 1: Write the failing tests.**

```python
async def test_rule_counts_saves_a_fact_and_settles_matching_bids(...):
    # two borderline bids with the same question; FakeRulings matches the new fact for the second
    changed = (await client.post(f"/api/runs/{rid}/rule/2", json={"decision": "counts"})).json()["changed"]
    assert {b["b_id"]: (b["state"], b["ruling"]["by"]) for b in changed} == {2: ("relevant", "you"), 3: ("relevant", "earlier")}
    assert [f["kind"] for f in (await client.get("/api/memory")).json()].count("ruling") == 1

async def test_undo_of_a_click_removes_its_fact_and_unsettles(...):
    # after the above: DELETE /rule/2 → 2 and 3 are borderline again; ruling fact gone from /api/memory; 2.undone

async def test_an_undone_bid_is_not_settled_again_by_a_later_ruling(...):

async def test_a_contradicting_ruling_replaces_the_old_fact(...):
    # existing fact matched with decision "counts"; rule "not" → old fact removed, new fact active

async def test_restore_of_a_doesnt_count_bid_changes_the_ruling(...):
    # rule "not" → POST /restore → state relevant, ruling.decision == "counts"

async def test_rule_on_a_bid_that_is_not_borderline_is_409(...):          # Review Focus 3
    # outcome unchanged afterwards

async def test_rule_and_undo_on_another_users_run_are_404(...):           # Review Focus 4

async def test_a_failing_fact_writer_is_502_and_changes_nothing(...):     # Review Focus 5
    # FakeRulings.write_fact raises → 502; /api/memory has no ruling; the outcome has no ruling; the sweep never ran

async def test_ruling_while_the_run_is_going_loses_no_results(...):
    # BlockingCollector run; rule bid 1 while bid 2 is blocked; release; outcomes has both, bid 1 ruled
```

Update `test_restore` for the 200 `{"changed": [...]}` response.

- [x] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/app tests/graph -q`. Expected: FAIL.

- [x] **Step 3: Implement** the manager methods and routes listed under Interfaces.

- [x] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/app tests/graph -q`. Expected: PASS.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`. Expected: all pass.

---

### Task 7: Borderline section in the web UI

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/http.ts`, `web/src/api/mock.ts`
- Modify: `web/src/components/ResultsPanel.tsx`, `web/src/components/ResultsPanel.css`
- Modify: `web/src/App.tsx`
- Test: `web/src/components/ResultsPanel.test.tsx`, `web/src/App.test.tsx`

**Interfaces:**
- Consumes: the Task 6 endpoints and the Task 4 `bid_result` fields
- Produces:
  - `BidResult` gains:
    - `state: 'relevant' | 'borderline' | 'quarantined'`
    - `question: string`
    - `ruling: { decision: 'counts' | 'not'; by: 'you' | 'earlier'; fact: string } | null`
  - `MemoryFactView` gains `kind: 'general' | 'ruling'` and `topic: string`
  - `TenderApi` changes:
    - adds `rule(runId: string, bId: number, decision: 'counts' | 'not'): Promise<BidResult[]>`
    - adds `unrule(runId: string, bId: number): Promise<BidResult[]>`
    - `restore` now returns `Promise<BidResult[]>`
  - `ResultsPanel` gains `onRule(b: BidResult, d: 'counts' | 'not')` and `onUnrule(b: BidResult)` props

- [x] **Step 1: Write the failing tests.**

```tsx
test('borderline bids sit above the relevant ones with their question and two buttons', ...)
  // run with one borderline + one relevant: heading "Borderline: your call"; the question text is shown;
  // buttons "Counts for me" and "Doesn't count"; tab label contains "Relevant 1" and "1 to decide"
test('clicking a ruling button calls onRule', ...)
test('a bid decided by an earlier ruling shows the ruling and an undo link', ...)
  // text 'decided by your earlier ruling: "AI that is…"'; clicking "undo" calls onUnrule
test('the zip link is hidden when no bid is relevant, even with borderline bids', ...)
test('memory lists a ruling fact with its topic', ...)
```

In `App.test.tsx`: after clicking "Counts for me", the bids returned by the mocked `rule` replace the matching rows. Do the same for restore.

- [x] **Step 2: Run the tests and confirm they fail.** Run `cd web && npx vitest run`. Expected: FAIL.

- [x] **Step 3: Implement.**
  - In `ResultsPanel`, split the rows by `state` instead of `relevant`. The Borderline section renders only when it has bids.
  - In `App`, add one helper, `mergeChanged(run, changed)`, keyed by `b_id`, used by `rule`, `unrule` and `restore`.
  - Add a mock implementation of the new API methods in `mock.ts`.
  - Style the new section with the existing tokens in `ResultsPanel.css`, using the warning colour for the question.

- [x] **Step 4: Run the tests and confirm they pass.** Run `cd web && npx vitest run`. Expected: PASS. Also run `npm run build` and confirm there are no type errors.

- [x] **Step 5: Checkpoint.** Run `uv run pytest -q`, then `cd web && npx vitest run`. Expected: all pass.

---

### Task 8: Eval measures borderline, and the spec is updated

**Files:**
- Modify: `gem_agent/eval/labelled.py` (`Score`, `score`, `report`, `rows_json`)
- Modify: `docs/GEM_TENDER_AGENT.md` (4.1, 5.4, 5.5, 13), `docs/APP_FLOW.md` (the Judge node and the UI node)
- Test: `tests/eval/test_labelled.py`

**Interfaces:**
- Consumes: `Verdict.borderline` (Task 1)
- Produces: `Score` gains `borderline: int` (borderline verdicts), `unsure: int` (unsure labels judged), `borderline_on_unsure: int`, and `unsure_judged_borderline: int`. The counting rules:
  - `tp = label.relevant and v.relevant`
  - `fp = not label.relevant and v.relevant`
  - `found_borderline = label.relevant and v.borderline`
  - `fn = label.relevant and not v.relevant and not v.borderline`
  - `precision = tp / (tp + fp)`
  - `recall = (tp + found_borderline) / (tp + found_borderline + fn)`
  - Disagreements leave out borderline verdicts. Borderline verdicts get their own section: `"Borderline (question):"`, one line each with the label and the question.

- [x] **Step 1: Write the failing test.**

```python
def test_borderline_counts_as_found_but_not_as_relevant():
    rows = [row(label=True, verdict=rel), row(label=True, verdict=border), row(label=False, verdict=border, unsure=True),
            row(label=True, verdict=no)]
    s = score(rows)
    assert (s.tp, s.fp, s.fn, s.borderline) == (1, 0, 1, 2)
    assert s.precision == 1.0 and s.recall == 2 / 3
    assert s.borderline_on_unsure == 1
    assert "Borderline (question):" in report(rows)
```

- [x] **Step 2: Run the test and confirm it fails.** Run `uv run pytest tests/eval -q`. Expected: FAIL.

- [x] **Step 3: Implement** the scoring and the report. Then update the spec:
  - 4.1: the Borderline section
  - 5.4: the three-way verdict and the question
  - 5.5: ruling facts, which are per topic, active at once, kept out of the Chat agent's prompt, and where the newest wins
  - 13: the borderline rate and agreement with unsure labels as measures

  Add one line to `APP_FLOW.md`'s steps.

- [x] **Step 4: Run the test and confirm it passes.** Run `uv run pytest tests/eval -q`. Expected: PASS.

- [~] **Step 5: Checkpoint.** _(suites pass; the manual browser check and the live eval are NOT done yet: they need a person and a paid model run)_ Run `uv run pytest -q` and `cd web && npx vitest run`. Expected: all pass.
  - **Manual check:** restart the app and run "AI platform tenders" in the web chat. Expect borderline bids, each with a specific question. Rule one, and check that similar bids in the run are settled and that `/api/memory` shows the ruling under "AI platform".
  - **Eval:** run `uv run gem-agent eval --topic ai` and report the borderline rate against the 8 unsure AI labels.
