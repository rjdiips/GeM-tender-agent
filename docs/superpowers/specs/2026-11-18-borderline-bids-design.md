# Borderline bids: the user decides — Design

Date: 18 Nov 2026. Status: draft for approval. Relates to spec sections 4.1 (web chat), 5.4 (Judge), 5.5 (user memory), 6.3 (verdict cache), 13 (evaluation).

## Why

Some tenders are neither clearly relevant nor clearly not. Whether they count depends on what the user sells. Examples from the labelled set's unsure items:
- Oil India's digital twin, where AI/ML is one part of the purchase. Does it count as an "AI platform"?
- An AI video-analytics application bought with a GPU server.

Today the Judge must answer yes or no, so each of these cases is a guess, and either kind of wrong guess costs the user. These bids go into a **Borderline** section instead, showing the question they raise. The user decides each one. Their decision is remembered, so the same kind of bid is decided for them in later runs.

## Goals

- The Judge says **borderline** when whether the tender counts depends on the user, and states the question the user has to answer.
- Borderline bids appear in their own section of the Relevant tab. The user rules with one click: **Counts for me** or **Doesn't count**.
- Each ruling is saved at once as a memory fact for that user. It settles other bids in the same run that raise the same question, including bids judged after the click. It decides similar bids in later runs on the same topic *(user's decisions: rule during the run, same topic only)*.
- Bids the user hasn't ruled on stay out of the zip and the relevant count *(user's decision)*.

## Non-goals

- The chat never asks. A run never pauses or waits for a ruling (the spec's "never asks" rule, 4 and 5.1).
- No sharing of rulings between users. Memory stays per user (5.5).
- No change to triage. Triage still only drops clear noes; it cannot say borderline, because it reads too little text to raise a precise question.
- Wiring up the post-run memory step (14.1) is separate work. Rulings don't need it.

## Anti-goals (failure even if it works)

- **Borderline becomes a hedge.** If the Judge marks many bids borderline, the user is back to reading everything. The borderline rate is measured on the labelled set (see Evaluation). A rate far above the share of unsure labels (16 of 90) is a failure.
- **Leaking between users.** One user's ruling must never change what another user sees for the same bid.
- **Vague questions.** A question the user can't answer without opening the documents ("Is this relevant?") defeats the purpose. The question must name the specific thing in doubt.

## Constraints

- **Verdicts are shared.** The verdict cache is keyed by (b_id, topic key, document hash) and shared across users and runs (6.3). The Judge's verdict must stay independent of any user.
- **Every field in a model-facing schema is required** (build step 5 rule): the server enforces the schema, and the model leaves out fields that have defaults.
- **Free text is length-bounded** (16 Nov rule), so a long answer can't cut the JSON off.
- **Existing cached verdicts** have no borderline field. They load as yes/no verdicts that are not borderline. Re-judging after the instruction change follows 6.3: judge into a separate verdict store and copy the results across.

## Approaches considered

1. **A user-neutral Judge plus a per-user step that applies rulings** (chosen 18 Nov). The Judge's answer becomes three-way, and a borderline answer must include a question. After judging, one short model call per borderline bid checks the user's facts and applies any earlier ruling that answers it. The shared verdict cache keeps working. Cost: one small call per borderline bid.
2. **A user-aware Judge.** The user's facts go into the Judge's prompt. Rejected: verdicts would become per user, the cache would lose sharing, every fact change would make verdicts stale, and it would be harder to show which ruling decided a bid.
3. **Guessing borderline from existing signals** (low confidence, quotes not found). Dropped: the model's confidence is almost always ≥0.85 (17 Nov), and this approach can't produce the question the user needs to answer.

## Design

### 1. Judge: three-way decision

- `JudgeOutput.relevant: bool` becomes `decision: Literal["relevant", "borderline", "not_relevant"]`, plus a required `question` field (max ~200 chars, empty unless the decision is borderline).
- The stored `Verdict` gains `borderline: bool` and `question: str`, both defaulting to false/empty so old verdicts still load. `relevant` stays: it is true only for "relevant". Code that reads `relevant` (views, zip, eval, run list) therefore keeps borderline bids out by default.
- Added to the instructions: say borderline only when the documents show something close to the topic and whether it counts is a matter of the buyer's purpose or the user's line of business, not a lack of evidence. The question names the specific thing in doubt ("AI is one part of a digital twin platform. Does that count as an AI platform for you?"). If the passages simply don't settle the matter, keep the existing low-confidence path.
- Evidence rules are unchanged: 1–3 verified quotes, which should show the part that makes the case borderline.
- The deep read (confidence below 0.6) still runs first. A verdict that is still borderline after the deep read goes to the user. *(My call: the deep read may settle it from the documents, which costs the user nothing.)*

### 2. Per-user resolution step

- This runs inside the graph's `process` node, right after the Judge, only for borderline verdicts. It's a new small agent (e.g. `agents/rulings.py`). It receives the topic, the bid's question and reason, and the user's ruling facts (below). It answers which fact, if any, decides the question, and which way.
- If a fact decides it, the outcome gets a ruling `{decision, by: "earlier", fact_id}`. If none does, the outcome stays borderline and unruled.
- Its result is stored on the run's outcome, never in the shared verdict cache.

### 3. Outcome and ruling storage

- `Outcome` gains `ruling: Ruling | None`, where `Ruling = {decision: "counts" | "not", by: "you" | "earlier", fact_id: str, at: datetime}`.
- **Effective state** of a bid, computed in one place (in `views.py`), with the first rule that applies winning:
  1. Restored from quarantine → relevant.
  2. A ruling of "counts" → relevant; a ruling of "not" → quarantined, with reason *"Your ruling: …"*.
  3. Borderline with no ruling → borderline.
  4. Otherwise the verdict decides.
- The zip, the relevant count, the run list's `found`, and the final report all use this effective state. Unruled borderline bids are excluded.

### 4. Ruling from the UI

- New endpoint: `POST /api/runs/{run_id}/rule/{b_id}` with `{decision: "counts" | "not"}`. It is owner-checked like `/restore`.
- What it does:
  1. One short model call turns the question and the answer into a **general fact**, so it applies to similar bids rather than only this one. Example: "AI that is one part of a larger system (digital twin, video analytics) counts as an AI platform."
  2. The fact is saved through `MemoryStore.add_stated`, marked as coming from a ruling (evidence: "you ruled on GEM/2026/B/7914442 on 18 Nov"). It is active at once; the two-run rule does not apply.
  3. The ruling `{by: "you"}` is set on this outcome.
  4. The resolution step (2) runs over the run's other unruled borderline bids, using only the new fact.
  5. A `signal` event `{kind: "ruled", b_id, decision}` is appended, like `restored`.
  6. The endpoint returns every bid whose state changed, and the browser updates them in place, as restore does today (`App.tsx`). A finished run has no live stream, so `result` events alone would reach nobody. *(My call, from the review.)*
- **Contradicting rulings: the newest wins.** If the new ruling answers a question an existing ruling fact already answered, but the other way, the old fact is removed (evidence: "replaced by your ruling on … on …"). The resolution step therefore never sees two facts that disagree. Bids that the old fact settled in earlier runs keep their state; runs are a record. *(My call.)*
- **A bid the user has undone stays theirs.** In that run, a later ruling on another bid never settles it automatically again. *(My call.)*
- **Restoring a "Doesn't count" bid from Quarantine changes the ruling to "counts".** It goes through the same path as a click, including replacing the fact. A restore and a fact that disagree would otherwise keep quarantining similar bids in later runs. *(My call.)*
- **Undo** (`DELETE /api/runs/{run_id}/rule/{b_id}`) clears that bid's ruling. If the click created the fact, the fact is removed and the other bids that fact settled in this run go back to Borderline. *(My call: a mis-click shouldn't leave a wrong fact behind.)* A fact the user later forgets in the Memory tab doesn't change runs that already happened.
- **Ruling facts** are memory facts that carry a marker (e.g. `kind: "ruling"` on `MemoryFact`, defaulting to "general"). The resolution step receives only ruling facts *(my call: fewer, sharper inputs)*.
- **The Chat agent does not see ruling facts.** They are left out of `prompt_facts` and don't count toward its limit of 20. *(My call, from the review.)* Two reasons:
  - a burst of rulings would push business facts out of the Chat agent's prompt;
  - the Chat agent could fold a ruling into the topic definition. That would change the topic key and make the Judge judge again with a topic shaped by one user. The bid would then be decided without showing which ruling decided it, and without an undo.

  Ruling facts are still listed in the Memory tab, where the user can edit or forget them.

### 4a. Ruling while the run is going (user's decision, 18 Nov)

- The buttons work as soon as a borderline bid appears, while the run is still going.
- The resolution step in `process` reads the user's ruling facts again for **each** bid, not once at the start. A bid judged after a click is then settled by that click's fact.
- Undo reaches every bid in the run that the fact settled, whether that happened in the click's own sweep (4.4) or later in `process`. Each ruling records its `fact_id`, so these bids are found by that id.
- **Writes to the run's results are serialised.** `RunStore.save_outcome` reads, changes and writes back the whole `outcomes.json`, and the rule endpoint waits on model calls between reading and writing. So:
  - writes take a per-run lock (a file lock, as `index/sync.py` does);
  - the endpoint re-reads the outcomes inside the lock after its model calls;
  - no model call is made while the lock is held.

### 4b. Rulings are tied to their topic (user's decision, 18 Nov)

- Each ruling fact records the topic it was made under. It stores the topic's short name (`chat.short_name`, e.g. "Loan Origination System") and its meaning.
- The resolution step receives only ruling facts whose topic matches the bid's topic. Matching is by short name, not the full topic key, so a slightly reworded definition of the same topic still matches. *(My call: the key changes with every word of the definition.)*
- The Memory tab shows each ruling fact with its topic.

### 5. UI

```
Relevant 12 · Borderline 3 · Quarantine 40
──────────────────────────────────────────
BORDERLINE: your call
 GEM/2026/B/7914442 · Oil India · closes 2d
 Digital twin platform with an ML layer.
 ❓ AI is one part of a digital twin. Does that count as an AI platform for you?
   "Combining Machine Learning & physics-based…" (RFP p.119)
   [ Counts for me ]  [ Doesn't count ]
──────────────────────────────────────────
RELEVANT
 GEM/2026/B/8028062 · Indian Army · …
 GEM/2026/B/7986029 · Indian Navy · …   decided by your earlier ruling: "AI that is…"  [undo]
```

- The Borderline section sits at the top of the Relevant tab, and only when it has bids. The tab counter shows both numbers.
- A bid decided by an earlier ruling shows that ruling's text and an undo link. Undo here clears only this bid's ruling; the fact stays.
- After a run that has unruled borderline bids, the chat thread gets one line: *"3 bids need your call: see Borderline."* No other prompt.
- `bid_result` gains `borderline`, `question` and `ruling`. `types.ts` and `ResultRow` follow.

### 6. Evaluation

- The labelled set is unchanged. Its unsure labels are the reference for "a person would call this borderline".
- `score()` adds:
  - the **borderline rate**: borderline verdicts out of all bids judged
  - **agreement with unsure labels**: of the borderline verdicts, how many are on unsure labels, and of the unsure labels, how many were judged borderline
- **Recall** counts a borderline verdict as found, since it reaches the user. **Precision** counts it as not relevant. *(My call: borderline bids are never shipped unasked, so they shouldn't inflate precision, and they aren't lost, so they shouldn't hurt recall.)*
- Disagreements show borderline verdicts with their question.
- Done means:
  - recall does not drop
  - precision does not drop
  - the borderline rate is close to the share of unsure labels
  - every borderline question names a specific thing in doubt (checked by reading them)

## Left to the builder

- Exact field names, endpoint shapes and component structure, following existing patterns (`restore` for the endpoint and signal, `bid_result` for views).
- Prompt wording for the Judge's borderline rule, the fact writer and the resolution step. Tune these on the labelled set.
- Which verdict store the re-judging writes to, and when results are copied across (6.3 procedure).
- Tests:
  - the views' effective-state rules, and the zip excluding unruled borderline bids
  - ruling and undo: the fact is created and removed, and other bids are settled and unsettled, including bids judged after the click
  - concurrent writes: a ruling arriving while `process` saves outcomes loses nothing
  - topic scoping: a ruling under one topic never settles another topic's bid
  - user isolation: one user's ruling never affects another user's run
  - every field of the new schemas is required
- Updating the spec: 4.1 (Borderline section), 5.4 (three-way verdict), 5.5 (ruling facts), 13 (new measures).
