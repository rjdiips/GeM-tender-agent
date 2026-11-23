"""A/B the passage rankers on the labelled set's quoted pages.

The only places embeddings and the reranker are used are `find_passages` (the Judge's
page ranking) and `LookupCatalog.find_categories` (the Planner's category labels). This
script exercises the first one offline: for every labelled bid/topic it runs
`find_passages` over that bid's cached chunks and asks whether the page the verdict
quoted lands in the top 8 — the metric the spec's 18-bid table used.

No model calls to the LLM, no crawling, no verdict cache: chunks come from
`data/cache`, so a run is deterministic and a model can be swapped per run.

Examples
--------
    # words only (baseline)
    uv run python eval/retrieval_ab.py --name words

    # the embedding server on the model host
    uv run python eval/retrieval_ab.py --name bge-small \
        --embed-url http://10.0.10.11:8080/v1 --embed-model BAAI/bge-small-en-v1.5

    # plus a reranker
    uv run python eval/retrieval_ab.py --name bge-small+rr-base \
        --embed-url http://10.0.10.11:8080/v1 --embed-model BAAI/bge-small-en-v1.5 \
        --rerank-url http://10.0.10.11:8081 --rerank-model BAAI/bge-reranker-base

    # local models (loads torch; bge-m3 on CPU is ~2.5 chunks/s)
    uv run python eval/retrieval_ab.py --name minilm --embed-model sentence-transformers/all-MiniLM-L6-v2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np

from gem_agent.agents.schemas import TopicDefinition
from gem_agent.cache.docs import DocCache
from gem_agent.retrieval.chunks import Chunk, make_chunks
from gem_agent.retrieval.embed import BgeM3Embedder, Embedder, RemoteEmbedder, SmallEmbedder, pick_device
from gem_agent.retrieval.passages import find_passages
from gem_agent.retrieval.rerank import BgeReranker, RemoteReranker, Reranker

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "eval/labelled_set.json"
CACHE = ROOT / "data/cache"


class MemoEmbedder:
    """Embedder wrapper that encodes each distinct text once, and keeps the vectors on disk.

    Topics share a bid's chunks, and a cancelled or repeated run should not pay for them
    again: `<cache-dir>/<model>.npz` holds sha1(text) -> float32 vector.
    """

    def __init__(self, inner: Embedder, cache_file: Path | None = None) -> None:
        self.inner, self.model_name, self._rows, self.calls = inner, inner.model_name, {}, 0
        self._cache_file, self._dirty = cache_file, 0
        if cache_file is not None and cache_file.exists():
            with np.load(cache_file) as data:
                self._rows = {k: data[k] for k in data.files}

    def _flush(self) -> None:
        if self._cache_file is None or not self._dirty:
            return
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self._cache_file, **self._rows)
        self._dirty = 0

    def encode(self, texts: list[str]):
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        missing = [(t, k) for t, k in zip(texts, keys, strict=True) if k not in self._rows]
        if missing:
            self.calls += 1
            vectors = self.inner.encode([t for t, _ in missing])
            for (_, key), vector in zip(missing, vectors, strict=True):
                self._rows[key] = vector.astype("float32")
            self._dirty += len(missing)
            if self._dirty >= 5000:
                self._flush()
        return np.asarray([self._rows[k] for k in keys], dtype="float32")

    def close(self) -> None:
        self._flush()


def build_embedder(url: str, model: str) -> Embedder | None:
    if not model:
        return None
    if url:
        return RemoteEmbedder(url, model, timeout_s=120)
    if "minilm" in model.lower():
        return SmallEmbedder(model)
    return BgeM3Embedder(model, device=pick_device("auto"))


def build_reranker(url: str, model: str) -> Reranker | None:
    if not model:
        return None
    if url:
        return RemoteReranker(url, model, style="tei", timeout_s=120)
    return BgeReranker(model)


def cases(limit_bids: int, topic: str, relevant_only: bool = False, buckets: set[str] | None = None) -> list[dict]:
    data = json.loads(LABELS.read_text())
    cache, out, seen_bids = DocCache(CACHE), [], []
    for item in data["items"]:
        if not (item.get("file") and item.get("page")):
            continue
        if topic and item["topic"] != topic:
            continue
        if relevant_only and not item.get("relevant"):
            continue
        if buckets and item["bucket"] not in buckets:
            continue
        if limit_bids and item["b_id"] not in seen_bids and len(seen_bids) >= limit_bids:
            continue
        files = cache.load(item["b_id"])
        if not files:
            continue
        record = next((f for f in files.readable() if f.filename == item["file"].strip()), None)
        if record is None or record.role == "gtc":
            continue
        if item["page"] not in [p.page for p in cache.load_text(record)]:
            continue
        if item["b_id"] not in seen_bids:
            seen_bids.append(item["b_id"])
        out.append(item | {"topic_def": data["topics"][item["topic"]]})
    return out


def rank_of(picked: list[Chunk], filename: str, page: int) -> int | None:
    for i, chunk in enumerate(picked, start=1):
        if chunk.filename == filename and chunk.page == page:
            return i
    return None


def run(case_list: list[dict], embedder, reranker, top_k: int, pool: int) -> list[dict]:
    cache, chunks_by_bid, rows = DocCache(CACHE), {}, []
    for n, item in enumerate(case_list, start=1):
        topic = TopicDefinition(**item["topic_def"])
        chunks = chunks_by_bid.get(item["b_id"])
        if chunks is None:
            files = cache.load(item["b_id"])
            chunks = chunks_by_bid[item["b_id"]] = make_chunks(files, cache) if files else []
        terms = [*topic.match_terms, *topic.acronyms]
        t0 = time.perf_counter()
        fused = find_passages(chunks, query=topic.meaning, terms=terms, top_k=max(top_k, pool),
                              embedder=embedder, rerank_pool=pool)
        fused_s = time.perf_counter() - t0
        ranked = fused
        if reranker is not None:
            t1 = time.perf_counter()
            ranked = find_passages(chunks, query=topic.meaning, terms=terms, top_k=max(top_k, pool),
                                   embedder=embedder, reranker=reranker, rerank_pool=pool)
            reranked_s = time.perf_counter() - t1
        else:
            reranked_s = 0.0
        row = {k: item[k] for k in ("topic", "b_id", "bid_number", "title", "bucket", "relevant", "unsure",
                                    "file", "page", "quote")}
        row |= {"rank_fused": rank_of(fused, item["file"].strip(), item["page"]),
                "rank_reranked": rank_of(ranked, item["file"].strip(), item["page"]),
                "chunks": len(chunks), "fused_s": round(fused_s, 3), "rerank_s": round(reranked_s, 3)}
        rows.append(row)
        if n % 25 == 0:
            print(f"  {n}/{len(case_list)} cases", flush=True)
    return rows


def hits(rows: list[dict], field: str, k: int) -> float:
    return sum(1 for r in rows if r[field] and r[field] <= k) / len(rows) if rows else 0.0


def mrr(rows: list[dict], field: str, cap: int) -> float:
    """Mean reciprocal rank over every case: a miss outside the cap counts as 0, not as excluded."""
    return statistics.fmean(1 / r[field] if r[field] and r[field] <= cap else 0.0 for r in rows) if rows else 0.0


def cluster_bootstrap(rows: list[dict], field: str, k: int, draws: int = 2000, seed: int = 7) -> tuple[float, float]:
    """95% CI, resampling bids (topics of one bid are not independent)."""
    import random

    rng = random.Random(seed)
    by_bid: dict[int, list[dict]] = {}
    for r in rows:
        by_bid.setdefault(r["b_id"], []).append(r)
    bids = list(by_bid)
    vals = []
    for _ in range(draws):
        sample = [r for b in (rng.choice(bids) for _ in bids) for r in by_bid[b]]
        vals.append(hits(sample, field, k))
    vals.sort()
    return vals[int(0.025 * draws)], vals[int(0.975 * draws)]


def report(name: str, rows: list[dict], top_k: int, pool: int, out: Path) -> None:
    print(f"\n== {name} ==  {len(rows)} cases, {len({r['b_id'] for r in rows})} bids")
    for field, k in (("rank_fused", top_k), ("rank_reranked", top_k), ("rank_fused", pool), ("rank_reranked", pool)):
        if field == "rank_reranked" and not any(r["rerank_s"] for r in rows):
            continue
        lo, hi = cluster_bootstrap(rows, field, k)
        print(f"  {field:14s} @{k:<2d}: {hits(rows, field, k):6.1%}  95% CI [{lo:.1%}, {hi:.1%}]  "
              f"MRR {mrr(rows, field, k):.3f}")
    for bucket in sorted({r["bucket"] for r in rows}):
        subset = [r for r in rows if r["bucket"] == bucket]
        print(f"  bucket {bucket:10s} n={len(subset):3d}  fused@{top_k} {hits(subset, 'rank_fused', top_k):6.1%}"
              f"  reranked@{top_k} {hits(subset, 'rank_reranked', top_k):6.1%}")
    for flag in ("relevant", "unsure"):
        for value in (True, False):
            subset = [r for r in rows if bool(r[flag]) is value]
            if subset:
                print(f"  {flag}={value!s:5s} n={len(subset):3d}  fused@{top_k} "
                      f"{hits(subset, 'rank_fused', top_k):6.1%}  reranked@{top_k} "
                      f"{hits(subset, 'rank_reranked', top_k):6.1%}")
    per_bid = {}
    for r in rows:
        per_bid.setdefault(r["b_id"], []).append(r["fused_s"] + r["rerank_s"])
    means = [statistics.fmean(v) for v in per_bid.values()]
    print(f"  latency per case: p50 {statistics.median(means):.2f}s  p95 {sorted(means)[int(0.95*len(means))-1]:.2f}s")
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"  wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True, help="tag for the result file")
    p.add_argument("--embed-url", default="")
    p.add_argument("--embed-model", default="")
    p.add_argument("--rerank-url", default="")
    p.add_argument("--rerank-model", default="")
    p.add_argument("--limit-bids", type=int, default=0)
    p.add_argument("--topic", default="")
    p.add_argument("--relevant-only", action="store_true", help="only items labelled relevant (the headline slice)")
    p.add_argument("--buckets", default="", help="comma-separated bucket filter, e.g. positive,trap,related")
    p.add_argument("--top-k", type=int, default=8)
    p.add_argument("--pool", type=int, default=24)
    p.add_argument("--out-dir", default=str(ROOT / "eval/retrieval_ab"))
    args = p.parse_args()

    buckets = {b.strip() for b in args.buckets.split(",") if b.strip()} or None
    case_list = cases(args.limit_bids, args.topic, args.relevant_only, buckets)
    if not case_list:
        raise SystemExit("no usable labelled cases (is data/cache populated?)")
    embedder = build_embedder(args.embed_url, args.embed_model)
    memo = None
    if embedder is not None:
        slug = embedder.model_name.replace("/", "_")
        memo = MemoEmbedder(embedder, Path(args.out_dir) / "cache" / f"{slug}.npz")
    reranker = build_reranker(args.rerank_url, args.rerank_model)
    rows = run(case_list, memo, reranker, args.top_k, args.pool)
    out = Path(args.out_dir) / f"{args.name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    report(args.name, rows, args.top_k, args.pool, out)
    if memo is not None:
        memo.close()
        print(f"  embedding calls: {memo.calls}")


if __name__ == "__main__":
    main()
