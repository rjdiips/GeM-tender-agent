# Pyright Cleanup Implementation Plan

> **For the implementing agent (DeepSeek via opencode):** Work this plan **one task at a time, in order**. After each task, STOP so the orchestrator (Claude) can verify before you start the next. Steps use checkbox (`- [ ]`) syntax — tick them as you go. Do not batch tasks together.

**Goal:** Bring `pyright gem_agent/` from 60 errors to 0 — fixing the genuine `None`/type bugs for real, and suppressing only the handful of true false positives with a documented reason.

**Architecture:** The 60 errors split into three categories that get **different** treatment. Applying one blanket fix (e.g. `# type: ignore` everywhere, or a guard everywhere) is wrong and will be rejected at verification. Each task handles one category or one file-cluster and is verified independently by running pyright on those files plus the test suite.

**Tech Stack:** Python 3.11, pydantic v2, pydantic-ai (`Agent`, `NativeOutput`, `create_model`), pymupdf, pytest + pytest-asyncio.

**Spec:** This plan is self-contained; the "spec" is the pyright error inventory captured in each task below (file:line + rule + root cause). Re-run `pyright --outputjson gem_agent/` to see the live list.

## Global Constraints

- **AGENTS.md rule is binding:** Fix type errors by correcting logic or the annotation. Do **not** use `# type: ignore`, bare `assert x is not None`, `cast(...)`, or a reflexive `if x else` guard *to silence a real bug*. Suppression is allowed **only** for a genuine false positive, and **only** with a one-line comment stating why (see Category C).
- **Behaviour must not change** except in the one place a latent crash is being fixed (Task 1). Every other edit is a type-narrowing that the runtime already satisfied — the existing test suite must stay green with identical results.
- **Verification per task is two commands, both required:**
  - `pyright <the files in this task>` → **0 errors** for those files.
  - `pytest` → **same pass/fail as before your change** (no new failures).
- **Do not edit any file not named in the task you are on.**
- Run pyright with the repo's installed binary: `pyright` (resolves to `~/.nvm/.../pyright`).

### The three categories (decision rule)

Before touching a line, classify it:

- **Category A — Real `None` bug or latent crash.** A value typed `X | None` is dereferenced/used and there is a real (or realistic) path where it is `None`. → **Fix the logic:** guard with an explicit `if x is None: return/raise/continue`, or add the missing `x is not None` / `x and` term to an existing boolean condition so the narrowing is truthful.
- **Category B — Correlated-guard false positive.** The value is provably non-`None` at the use site because an *earlier, correlated* condition guarantees it, but pyright can't follow the correlation across a comprehension or a separate boolean. → **Make the narrowing explicit in the same expression** (add the `x is not None` / `x and` term). Behaviour is identical; you are only telling pyright what the code already guarantees. This is NOT suppression.
- **Category C — Dynamic-type / library-stub false positive.** The attribute/type genuinely cannot be expressed statically (a `create_model(...)` dynamic pydantic model; a `pymupdf` stub missing `__iter__`). → **Add a typed boundary** (a `Protocol` describing the shape, then annotate/assign through it) **or**, matching the existing repo pattern (`rulings.py:40` already uses `# type: ignore[valid-type]`), a scoped `# pyright: ignore[<rule>]  # <one-line reason>`. Prefer the Protocol where it's cheap; suppress where the value is a one-off.

## Review Focus

- **Silencing a real bug (most likely failure).** A Category-A line gets a `# type: ignore` instead of a guard. The orchestrator will re-classify every suppression added; any suppression on a line that had a real `None` path is rejected.
- **A "behaviour-preserving" edit that changes a result.** Adding `and x` to a boolean can flip a branch if `x` is falsy-but-not-None (e.g. `0`, `""`, empty list). Check the type: for `datetime | None` / object `| None`, `and x` is safe; for `int | None` where `0` is valid, use `x is not None`, never truthiness.
- **The latent crash in Task 1 actually being reachable.** `ResolvedWindow` with a flag set but its date `None` must not raise; confirm the regression test exercises exactly that.
- **Dynamic-model Protocol drifting from the real fields.** If you add a `Protocol` for the `create_model` output, its fields must match the `create_model` field list exactly, or you've moved the bug, not fixed it.
- **pymupdf iteration fix changing page order/count.** If you replace `enumerate(doc)` with index iteration, pages must stay 1-based and in order.

---

### Task 1: `agents/schemas.py` — fix the latent `describe()` crash (2 errors)

This is the proof task: a genuine Category-A latent crash, fixed with a real guard and a regression test.

**Files:**
- Modify: `gem_agent/agents/schemas.py:178-181`
- Test: `tests/agents/test_schemas.py` (create if absent)

**Root cause:** `ResolvedWindow.describe()` guards on the `bool` flags `closing_after` / `closing_until` (schemas.py:163-164) but then dereferences the *different* `datetime | None` fields `closing_from` / `closing_to`. If a flag is ever set without its date, this raises `AttributeError`.

- [ ] **Step 1: Write the failing regression test** in `tests/agents/test_schemas.py`:

```python
def test_describe_closing_flag_without_date_does_not_crash():
    w = ResolvedWindow(closing_until=True, closing_to=None)
    assert w.describe() == "open now"      # falls through, no AttributeError
    w2 = ResolvedWindow(closing_after=True, closing_from=None)
    assert w2.describe() == "open now"
```

- [ ] **Step 2: Run it — expect FAIL** (AttributeError): `pytest tests/agents/test_schemas.py -v`
- [ ] **Step 3: Fix** schemas.py:178-181 by narrowing the actual field in each guard (match the pattern already used on lines 172-176):
  - `if self.closing_after and self.closing_from:` then use `self.closing_from`
  - `if self.closing_until and self.closing_to:` then use `self.closing_to`
- [ ] **Step 4: Run — expect PASS:** `pytest tests/agents/test_schemas.py -v`
- [ ] **Step 5: Verify pyright clean:** `pyright gem_agent/agents/schemas.py` → 0 errors
- [ ] **Step 6: Commit** (see Global Constraints for commit identity in AGENTS.md).

---

### Task 2: Correlated-guard narrowing — `agents/searcher.py` (2), `index/db.py` (3)

**Category B.** Behaviour-preserving; no new tests, existing suite must stay green.

**Files:**
- Modify: `gem_agent/agents/searcher.py:77`
- Modify: `gem_agent/index/db.py:204`

**Root cause & fix:**
- searcher.py:77 — `(clo and bid.closing < clo)` / `(chi and bid.closing > chi)`: `bid.closing` is `datetime | None`; the `not bid.closing` early-return on line 75 guarantees it's set here but pyright doesn't carry it. Add the explicit term: `(clo and bid.closing and bid.closing < clo)` (safe: `datetime` is never falsy).
- db.py:204 — `old[-3]`/`old[-2]`/`old[-1]`: `old` is `... | None`; the subscript is reached only when `tracked_same` is true, which already implies `old is not None`. Make it explicit: `if old is not None and tracked_same and old[-3] == ...`.

- [ ] **Step 1:** Apply the two narrowings above.
- [ ] **Step 2:** `pyright gem_agent/agents/searcher.py gem_agent/index/db.py` → 0 errors.
- [ ] **Step 3:** `pytest` → no new failures.
- [ ] **Step 4:** Commit.

---

### Task 3: `eval/labelled.py` `score()` — comprehension narrowing (13 errors)

**Category B**, with care. Behaviour-preserving.

**Files:**
- Modify: `gem_agent/eval/labelled.py:87-131`

**Root cause:** `ok = [r for r in rows if r.verdict is not None]` does not change the *element type* of `ok` (still `Row` with `verdict: Verdict | None`), so every later `r.verdict.borderline` / `.relevant` / `.stage` re-triggers the optional error, and the `sum(...)` generators become `bool | None` which breaks `sum`'s overload.

**Fix:** In each comprehension/generator over `ok` (and over `border`), add an explicit `r.verdict is not None and ...` (or `r.verdict and ...`) guard before any `r.verdict.<attr>` access. Because every element of `ok` already has a non-None verdict, this changes nothing at runtime but makes each generator yield a plain `bool`, fixing both the member-access errors and the `sum` overload errors together.

- [ ] **Step 1:** Add the explicit verdict guard to each flagged expression (lines 87-131).
- [ ] **Step 2:** `pyright gem_agent/eval/labelled.py` → 0 errors.
- [ ] **Step 3:** `pytest tests/eval -v` → scores identical to before (no new failures).
- [ ] **Step 4:** Commit.

---

### Task 4: Genuine `None` handling at call sites — `collector.py` (1), `cache/lookups.py` (1), `cache/names.py` (1), `jobs/backfill_queries.py` (4), `tools/gem/live.py` (4)

**Category A** (mostly). Each site assigns a value that can be `None`, then uses it. Read the surrounding function to decide the right handling per the decision rule.

**Files & sites:**
- `gem_agent/agents/collector.py:67` — `.complete_ok` on `None`
- `gem_agent/cache/lookups.py:83` — `.astype` on `None`
- `gem_agent/cache/names.py:279` — `.groups` on `None` (a regex `.match(...)` result not checked)
- `gem_agent/jobs/backfill_queries.py:50,57,66,70` — `.stage`/`.queries`/`.model_copy` on `None`
- `gem_agent/tools/gem/live.py:93,131,166,217` — `.get`/`.put`/`//` on `None`

**Decision rule per site:**
- Regex `.match()`/`.search()` result (names.py:279) → guard `if m is None: <return/continue/raise>` before `.groups`.
- An agent/step output or cache lookup that can legitimately be absent → early `return`/`continue` or `raise ValueError("...")` with a message, matching how the function already handles its empty cases.
- `live.py:166` `//` on `None` → the operand is `int | None`; use `is not None` (NOT truthiness — `0` is a valid int).

- [ ] **Step 1:** For each site, read the enclosing function, classify (is the `None` path real?), and apply a truthful guard. If a site turns out to be a correlated-guard false positive, treat it as Category B instead. Do **not** `# type: ignore` a real `None` path.
- [ ] **Step 2:** `pyright gem_agent/agents/collector.py gem_agent/cache/lookups.py gem_agent/cache/names.py gem_agent/jobs/backfill_queries.py gem_agent/tools/gem/live.py` → 0 errors.
- [ ] **Step 3:** `pytest` → no new failures.
- [ ] **Step 4:** Commit.

---

### Task 5: `X | None` passed where `X` is required, and `Literal` mismatches — `graph/build.py` (6), `graph/manager.py` (4), `index/migrate.py` (1), `tools/http_client.py` (1)

**Category A.** The fix is to guarantee the value before the call, or to tighten the source type / return type.

**Files & sites:**
- `graph/build.py:446,573,647` and `graph/manager.py:107,271,308` — a `Verdict | None` / `TenderRequest | None` / `str | None` reaches a parameter that requires the non-None type. Guard (`if x is None: raise/return`) before the call, or fix the upstream so the value is non-None by construction.
- `graph/build.py:405,538,550,647` — a plain `str`/`str | None` reaches a `Literal[...]` parameter. If the value is known-valid, validate it (`assert`-free): narrow via an `if value in {...}:` check or a small mapping, then pass the narrowed local. If it genuinely cannot be proven, this is Category C — suppress with a reason.
- `graph/manager.py:108` — a `dict` passed where a typed config is expected; construct the proper type or type the dict literal.
- `index/migrate.py:19` — `str | None` to a `str` param; guard the `None`.
- `tools/http_client.py:211` — function annotated `-> str` returns `str | None`; either guarantee a `str` (raise on `None`) or widen the return type to `str | None` and fix callers. Pick whichever matches the function's contract — read its callers first.

- [ ] **Step 1:** Fix each site per the rule above, reading callers where the contract is ambiguous (http_client, manager config).
- [ ] **Step 2:** `pyright gem_agent/graph/build.py gem_agent/graph/manager.py gem_agent/index/migrate.py gem_agent/tools/http_client.py` → 0 errors.
- [ ] **Step 3:** `pytest` → no new failures.
- [ ] **Step 4:** Commit.

---

### Task 6: Dynamic-model & library-stub false positives — `agents/planner.py` (9), `agents/rulings.py` (7), `tools/pdf/text.py` (1)

**Category C.** These cannot be expressed statically. Suppress with a documented reason, matching the existing `rulings.py:40` pattern — or add a `Protocol` where it's clean.

**Files & root cause:**
- `planner.py:44-46` (`reportInvalidTypeForm`) — `list[_choice(...)]` builds a `Literal` at runtime; a call in a type position is unavoidable. Suppress each with `# pyright: ignore[reportInvalidTypeForm]  # dynamic Literal built at runtime` (mirrors rulings.py:40).
- `planner.py:84-94` and `rulings.py:64-66` (`reportAttributeAccessIssue` on `BaseModel`) — `.output` is a `create_model(...)` dynamic model, so its fields aren't statically visible. **Preferred fix:** define a `Protocol` (`class _PlanOut(Protocol): ministries: list[str]; departments: list[str]; categories: list[str]; bid_types: list[int]; extra_terms: list[str]; reasons: str`) and bind `out: _PlanOut = cast(_PlanOut, (await agent.run(...)).output)` — fields **must** match the `create_model` list exactly. Same for rulings' resolve output (`fact_id: str; decision: str; reason: str`). If the Protocol feels heavy, a scoped `# pyright: ignore[reportAttributeAccessIssue]  # dynamic create_model output` is acceptable.
- `rulings.py:43` (`reportArgumentType`, `fact_id` tuple) and `rulings.py:63,73` (`model_settings` dict) — tighten the `create_model` field tuple / annotate the settings dict, or suppress with reason if dynamic.
- `pdf/text.py:39` — `enumerate(doc)` fails because pymupdf's `Document` stub lacks `__iter__`, though it iterates at runtime. **Preferred fix:** iterate by index — `for i in range(doc.page_count):` then `page = doc[i]` (keeps 1-based `page=i+1`). This removes the error without a suppression.

- [ ] **Step 1:** Apply Protocol/index-iteration fixes where clean; otherwise scoped `# pyright: ignore[<rule>]  # <reason>`. Every suppression carries a reason.
- [ ] **Step 2:** `pyright gem_agent/agents/planner.py gem_agent/agents/rulings.py gem_agent/tools/pdf/text.py` → 0 errors.
- [ ] **Step 3:** `pytest` → no new failures.
- [ ] **Step 4:** Commit.

---

### Task 7: Whole-package verification

- [ ] **Step 1:** `pyright gem_agent/` → **0 errors, 0 warnings**.
- [ ] **Step 2:** `pytest` → full suite green.
- [ ] **Step 3:** Confirm no new `# type: ignore` / `# pyright: ignore` exists outside Task 6's files (grep: `git diff --unified=0 | grep -n "ignore\["`). Every one must have a reason comment.
- [ ] **Step 4:** Final commit / ready for orchestrator review.
