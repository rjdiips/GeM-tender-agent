# SDD ledger — plan: /apps/rajdeep_workspace/gem_agent/docs/superpowers/plans/2026-11-18-borderline-bids.md

Spec: /apps/rajdeep_workspace/gem_agent/docs/superpowers/specs/2026-11-18-borderline-bids-design.md (reachable)
Worktree: /apps/rajdeep_workspace/gem_agent/.claude/worktrees/borderline-bids, branch worktree-borderline-bids, base 70a9e9c
Baseline: 360 pytest passed (10 deselected), 38 vitest passed.

## Pre-flight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1→T4,T5,T6,T8 | Verdict.borderline/question | consistent |
| T2→T3,T5,T6 | MemoryFact.kind/topic, add_ruling, ruling_facts, forget(evidence) | consistent; spec says fact stores short name AND meaning, plan stores short name only |
| T3→T5,T6 | RulingsAgent.resolve/write_fact, RulingMatch | consistent |
| T4→T5,T6,T7 | Ruling, Outcome.ruling/undone, update_outcomes, bid_state, bid_result(state/question/ruling), FakeJudge(borderline=) | consistent |
| T5→T6 | settle, topic_for, FakeRulings | consistent |
| T6→T7 | rule/unrule endpoints, restore → {"changed"} | consistent |
| T1 self | tests vs code | consistent |
| T2 self | MemoryFact gets defaults; MemoryFact is not a NativeOutput model (TypeAdapter storage only) | OK, defaults allowed |
| T3 self | — | consistent |
| T4 self | backfill_queries.py:49 reads `verdict.relevant or restored` → bid_state | consistent |
| T5 self | — | consistent |
| T6 self | restore→rule("counts") needs a borderline verdict; only borderline bids carry "not" rulings | consistent |
| T7 self | tab label "Relevant 1 · 1 to decide" vs spec mock "Borderline 3" | plan wording ok (spec leaves UI to builder) |
| T8 self | edits docs/, which is git-ignored | see ruling |
| Plan header | "not a git repository" | false |

Ruling: commit at every checkpoint on branch worktree-borderline-bids — the plan says to commit if git exists — cost if wrong: squash later.
Ruling: ruling facts store the topic short name only (no meaning) — the plan's Interfaces are explicit and the spec leaves field names to the builder; resolve gets topic.meaning from the TopicDefinition — cost if wrong: add a field later.
Ruling: Task 8 doc edits go to /apps/rajdeep_workspace/gem_agent/docs/ (absolute path, main checkout, git-ignored, uncommitted) — docs/ is ignored, so the worktree has no copy — cost if wrong: the user moves the edits.
Ruling: Task 8 manual browser check and live eval — attempt the eval only if model credentials exist; report honestly, otherwise list as not done.

## Tasks
Task 1: dispatched (base 70a9e9c, implementer sonnet, agent a9389ca7257769f60)
Task 1: complete (commits 70a9e9c..c19a016, review clean). Minor noted for final review: borderline verdict with unverified quotes gets no needs_review (plan-mandated).
Task 2: dispatched (base c19a016, sonnet, agent ab65c38e3c113e097)
Task 2: reviewed at c0f1ffe — Important: memory_step lists ruling facts/ids to model; apply acts on them. Fix round 1 sent.
Task 2: minor (deferred): prompt_facts limit test uses 1 general fact, could use 20+.
Task 2: fix round 1/5 (1 addressed, 0 open; commits c0f1ffe..94fec91)
Task 2: minor (deferred): apply drops an "add" change whose fact_id points at a ruling (was: created general fact). Controller checked: facts() re-reads disk (no aliasing); correct() doesn't use apply.
Task 2: complete (commits c19a016..94fec91, review clean)
Task 3: dispatched (base 94fec91, sonnet)
Task 3: previous implementer left uncommitted work, no report; re-dispatched to verify/finish (base 94fec91, sonnet)
Task 3: complete (commits 94fec91..f2421da, review clean). Minor deferred: FactOut.fact has no min_length (blank fact possible); write_fact "not" wording untested. ⚠️ render-includes-meaning covered by prompt test.
Task 4: dispatched (base f2421da, sonnet)
Task 4: implemented 3ef4179 (amended → 98ffcf7 for Opus co-author trailer); review dispatched.
Ruling: report counts restored bids as relevant (behaviour change via bid_state) — spec rule 1 says restored → relevant, and plan says report calls bid_state — cost if wrong: one-line revert in report.
Ruling: subagents keep writing a Sonnet co-author trailer from their own system reminder; controller amends each commit to the Opus line — session attribution reminder governs — cost if wrong: none.
Task 4: reviewed at 98ffcf7 — Important: backfill_queries writes back stale full outcome, can erase ruling/undone. Fix round 1 sent.
Task 4: minor (deferred → Task 6 owns restore): manager.restore read-then-save_outcome outside lock; Task 6 should use update_outcomes.
Task 4: minor (deferred): fcntl lock non-reentrant (fn calling save_outcome deadlocks), no docstring; no test the lock excludes writers.
Task 4: minor (deferred): no direct tests that run_list_item.found / backfill use bid_state.
Task 4: minor (deferred): old-outcome test fakes old JSON via string replace (brittle).
Task 4: fix round 1/5 (1 addressed, 0 open; commits 98ffcf7..68dec5b)
Task 4: complete (commits f2421da..68dec5b, review clean)
Task 5: dispatched (base 68dec5b, sonnet)
Session 2 (2026-11-18): resumed. Task 5 earlier dispatch left no commits/report; re-dispatching. Plan file now carries checkmarks + Progress section (update it at each task completion).
Task 5: re-dispatched (base 68dec5b, sonnet)
Task 5 implementer agent: adf131d799425c369
Task 5: implemented ac9e54d (DONE_WITH_CONCERNS: no captured RED run); review dispatched
Task 5: complete (commits 68dec5b..ac9e54d, review clean). Minor deferred: no test for unknown fact_id→None, decision "not", topic_for fallback; mid-run test uses sleep(0.2); unused TWO lambda (test_run_graph.py:291); no RED captured.
Task 6: dispatched (base ac9e54d, sonnet)
Task 6 implementer agent: a57f9c692f3a4b4d8
Task 6: implemented bca6a2f (DONE_WITH_CONCERNS); review dispatched (opus: lock/concurrency risk).
Ruling: rule() returns 502 RulingFailed when resolve fails or s.rulings is None (not only write_fact) — nothing is written, so it fails safe — cost if wrong: user retries; could fall back to writing a new fact.
Ruling: bids settled "earlier" by a fact that a contradicting ruling later replaces keep their ruling (stale fact_id) — spec silent; the user's newer ruling only sweeps unruled bids — cost if wrong: those bids show an outdated ruling until ruled again; note for final review.
Task 6: reviewed at bca6a2f — Important: re-ruling an already-ruled bid drops created_fact (undo leaves orphan fact + earlier settlements); replacing a fact strands bids it settled. Spec ❌: no tests in test_run_graph.py.
Ruling: tests for Task 6 may all live in tests/app/test_api.py — plan lists test_run_graph.py among test files but assigns no test to it; the API tests drive the manager end to end — cost if wrong: move tests.
Ruling: fix design — same-decision re-rule by you is a no-op ([]); a replaced fact (own or matched) clears "earlier" rulings pointing at it in this run, and those bids join the sweep for the new fact — cost if wrong: rework rule().
Task 6: minor (deferred): unrule/rule write fns don't re-check pre-lock reads; memory writes not rolled back if update_outcomes fails; untested 502 paths (resolve fail, rulings None), DELETE unknown 404, restore 409/502; restore→rule emits only "ruled"; rule resets undone=False.
Task 6: fix round 1 sent (resume implementer)
Task 6: fix round 1/5 (2 addressed, 0 open — re-rule no-op / replaced-fact resweep; commits bca6a2f..4c47eea)
Task 6: minor (deferred): stale clearing in write not limited to group; bid newly settled by replaced fact between snapshot and write goes borderline without resweep.
Task 6: complete (commits ac9e54d..4c47eea, review clean)
Task 7: dispatched (base 4c47eea, sonnet)
Task 7 implementer agent: acafd8a08bd0f5010
Task 7: implemented c4613e2; review dispatched
Task 7: minor (deferred): zip-hidden test lacks positive case; mergeChanged has no run_id guard (run switch mid-request); App undo test doesn't assert row replacement; mock rule/unrule don't persist; indentation App.test.tsx:133.
Task 7: complete (commits 4c47eea..c4613e2, review clean)
Task 8: dispatched (base c4613e2, sonnet)
Task 8 implementer agent: a7522069aa0e0e31f. Pre-edit doc copies saved in session scratchpad. Ruling: the implementer skips the manual browser check and live eval; the controller decides after the final review — the eval spends on a paid model and the browser check needs a person — cost if wrong: run later.
Task 8: implemented 88727df + uncommitted docs edits (diff vs backup: task-8-docs.diff); review dispatched.
Ruling: Score keeps both borderline_on_unsure and unsure_judged_borderline though the plan defines them identically — plan Interfaces name both — cost if wrong: drop one later; flag for final review.
Task 8: reviewed at 88727df — Important: §13 says eval reports borderline rate + unsure agreement; report() prints neither. ⚠️ agents/rulings.py path: confirmed exists. ⚠️ §5.4 step-5 parenthetical: confirmed by Task 1 plan text (deep read runs before a borderline verdict is stored).
Task 8: minor (deferred): duplicate unsure fields (ruled keep); header line length.
Task 8: fix round 1 sent (add rate/unsure line to report() + test; tighten guarded Disagreements assert)
Task 8: fix round 1/5 (1 addressed, 0 open — eval prints borderline rate + unsure agreement; commits 88727df..4c2c1a8)
Task 8: minor (deferred): unsure_judged_borderline duplicates borderline_on_unsure so agreement clauses can't disagree; report() calls scores() twice; long lines labelled.py:114,116; fix report lacks full-suite output (controller re-runs at finish).
Task 8: complete (commits c4613e2..4c2c1a8, review clean). Manual browser check + live eval NOT run (ruling above).
Final review: dispatched (opus, range 70a9e9c..4c2c1a8)
Controller verification at 4c2c1a8: pytest 403 passed (10 deselected); vitest 45 passed.
Final review (opus) at 4c2c1a8: With fixes. Important: (1) concurrent rule on same bid → orphan fact undo can't remove; (2) UI no pending/error state; (3) mergeChanged no run_id guard; (4) Memory tab not refreshed after rule/undo/restore, and correct() revives a removed fact.
Ruling: fix wave includes the 4 Important + cheap minors (blank fact → RulingFailed; correct() refuses removed facts; restore/opposite re-rule replaces the bid's own o.ruling.fact_id directly; drop duplicated fact line on "not" rows; docs 4.1 counter wording to match UI; user-isolation test) — cheap and user-visible — cost if wrong: small extra diff.
Ruling: parked for later (not in fix wave): process-vs-click race (one extra click), sequential sweep, case-sensitive topic match, restore 502 fallback, running_count label, backfill skips borderline, sidebar found refresh / poll overwrite, deploy re-judge step 6.3 unscheduled — real but not load-bearing; surfaced to user — cost if wrong: follow-up work.
Final fix wave: dispatched (opus, base 4c2c1a8)
Final fix wave: e32dda3 (+ uncommitted docs 4.1 edit). pytest 412, vitest 51, build ok.
Ruling: a contradicted fact is forgotten under newest-wins regardless of who created it; undo still forgets only the click's own fact — spec "newest wins" — cost if wrong: a reused fact disappears when contradicted.
Ruling: editing an unknown memory fact id now 404 (was silent 204) — clearer API — cost if wrong: one client relying on 204.
Ruling: UI pending state keyed by b_id only; a same-b_id row in another run stays disabled until the call returns — no wrong merge possible — cost if wrong: brief disabled button.
Final fix wave: re-review dispatched (range 4c2c1a8..e32dda3)
Final fix wave: re-review clean (9/9 addressed). Controller verified pytest 412, vitest 51 at e32dda3.
Final: parked minors — pending/rowErrors keyed by b_id only (late error can show on another run's same b_id); _rule_locks never pruned; model calls under per-run asyncio lock have no timeout; App edit/forget unhandled rejection on 404; add_ruling orphan if store write throws; asyncio lock is single-process (app runs one uvicorn worker; fcntl re-check is the cross-process net).
