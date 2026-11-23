# Which embedding model and reranker? (offline A/B)

`eval/retrieval_ab.py` answers the only question the model swap can change: does the
decisive page reach the Judge's top 8? It runs the real `find_passages` (BM25 + dense +
RRF, optional rerank) over chunks already in `data/cache`, so no crawling, no LLM calls
and no verdict cache are involved. Every run is deterministic except the model server.

## The call sites it exercises

| Site | Code | Corpus per call | What it decides |
|---|---|---|---|
| Judge topic passages | `judge.py:399` → `find_passages` | one bid's chunks: medians 169, 47 853 chunks over the 156 labeled bids | the 8 page chunks that reach the 13k-token verdict prompt |
| Judge queries passages | `judge.py:434` → `find_passages` | same chunks, no reranker | 2 passages around the queries date |
| Planner categories | `lookups.py:85` → `find_categories` | ~GeM category labels (one `.npy`, keyed by model) | 15 category suggestions; **currently built without an embedder**, so lexical-only in the app |

Reranker: only the first row, over the fused top 24.

## Test bed

- `eval/labelled_set.json`: 420 labelled topic×bid items, 260 with a quoted `file` + `page`.
- 247 of those resolve to readable pages in `data/cache` (156 bids, 38 relevant, 29 unsure,
  28 traps, 137 live-only negatives).
- Target: `(filename, page)` of the quote. Metric from spec 5.4: hit@8, plus hit@24, MRR
  and per-bid latency. CIs are cluster bootstrap over bids, because topics of one bid are
  not independent.

## Running

```bash
# baseline, ~45 s
uv run python eval/retrieval_ab.py --name words

# an embedding server (the TEI container on the model host)
uv run python eval/retrieval_ab.py --name bge-small \
  --embed-url http://10.0.10.11:8080/v1 --embed-model BAAI/bge-small-en-v1.5

# add a reranker
uv run python eval/retrieval_ab.py --name bge-small+rr-base \
  --embed-url http://10.0.10.11:8080/v1 --embed-model BAAI/bge-small-en-v1.5 \
  --rerank-url http://10.0.10.11:8081 --rerank-model BAAI/bge-reranker-base

# local MiniLM (in-process, what the app falls back to)
uv run python eval/retrieval_ab.py --name minilm \
  --embed-model sentence-transformers/all-MiniLM-L6-v2

# a slice, for a slow model
uv run python eval/retrieval_ab.py --name bge-m3-slice --limit-bids 40 \
  --embed-url http://10.0.10.11:8080/v1 --embed-model BAAI/bge-m3
```

Results land in `eval/retrieval_ab/<name>.jsonl`, one row per case with `rank_fused` and
`rank_reranked`. Compare two files with any JSONL tool; the same case set is used, so the
comparison is paired.

## Results (20 Nov, spec 3.5.1)

Relevant cases (38 cases, 34 bids), metric = hit@8 of the quoted page:

| Configuration | hit@8 | hit@24 | MRR@24 | p50 / bid |
|---|---|---|---|---|
| words only | 36.8% | 42.1% | 0.204 | 0.1 s |
| MiniLM, in-process | 47.4% | 65.8% | 0.297 | 1.9 s |
| bge-small over TEI | 47.4% | 60.5% | 0.218 | 4.3 s |
| bge-small + bge-reranker-base | 39.5% | 60.5% | 0.261 | +4.0 s |
| bge-small + bge-reranker-v2-m3 | 39.5% | 60.5% | 0.261 | +16 s |

Full set (247 cases): words 9.3% → bge-small 19.0% hit@8; reranker-base 17.0% (42/247 — 5 targets
demoted out of the top 8). Conclusion: a dense embedder is worth it, MiniLM is the best of them here,
and the reranker is not.

## Reading the result

1. **`fused@24` vs `fused@8`** — if the target is rarely in the 24, no reranker can fix it;
   the embedding model is the lever. If it is in the 24 but not the 8, the reranker is.
2. **`relevant=True`** is the head number; `trap` and `live_only` guard against a model
   that only gets better at agreeing with the label.
3. A model is worth swapping only if the relevant-True hit@8 moves beyond the bootstrap
   CI overlap, or if it buys latency at the same hit rate.
4. Latency must fit 3–10 s per bid; the Judge calls the ranker once per bid (plus once for
   the queries passages) so ~169 chunks + 2 queries per bid is the production load.

## Caveats before trusting a verdict

- The Judge's prompt is more than the top 8: bid pages 1–2, scope passages, opening page
  of every attachment and the queries passages are always added. A ranking win may not
  change a verdict that was already right, and a ranking loss may be covered by those.
  Confirm with the end-to-end run below before deciding on a close call.
- **The verdict cache does not include the model.** `VerdictCache` is keyed by
  `(b_id, topic_key, doc_hash)` (`judge.py:216`), so a re-judge after a model swap returns
  the old verdict. Point `DATA_DIR` at a scratch directory or clear `data/cache/verdicts`
  first.
- The category-vector cache (`data/cache/lookups/categories.<sha1>.npy`) *is* keyed by
  model name, so it is safe to leave.
- No sampling settings are set on the chat model, so vLLM's defaults apply: end-to-end
  verdicts are not deterministic. Repeat a subset 2–3 times, or cap at temperature 0 on
  the server, before attributing a difference to the ranker.

## End-to-end confirmation (only for the top two)

On a fixed subset (e.g. 40 bids spanning positives, traps and live-only), run the Judge
with each model and compare against `relevant` in the labelled set: recall on relevant
bids, false positives on `trap`/`related`, and the borderline rate. Cost is ~2–3 model
calls per bid; this is the only stage that needs the LLM and the only one where the
reranker's effect on the final verdict is visible.
