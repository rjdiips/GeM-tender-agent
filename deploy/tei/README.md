# TEI embed + rerank on the model host (CPU)

The app runs on another machine and calls these two services over the LAN:

| Service | Container | URL the app uses | Model |
|---|---|---|---|
| embeddings | `gem-tei-embed-1` | `http://10.0.10.11:8080/v1/embeddings` | `BAAI/bge-small-en-v1.5` (384 dims) |
| rerank | `gem-tei-rerank-1` | `http://10.0.10.11:8081/rerank` | `BAAI/bge-reranker-base` |

Both run on **CPU**. The RTX 4090 here is claimed by vLLM at
`--gpu-memory-utilization 0.95` (~0.9 GB free), so GPU containers cannot start, and
lowering vLLM is ruled out (spec 3.5). The small models keep the 3–10 s per bid budget:
the CPU fallback measured words + MiniLM at 12/18 top-8 hits in 6 s, versus 10/18 in
423 s for bge-m3 on CPU.

## Start / stop

```bash
cd deploy/tei
docker compose up -d
docker compose ps
docker compose logs -f embed rerank
```

The first start downloads the ONNX weights into the `gem-tei_tei-models` volume.

## Check from this host

```bash
curl -s localhost:8080/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"BAAI/bge-small-en-v1.5","input":["bank IT tender"]}' | head -c 300; echo

curl -s localhost:8081/rerank -H 'Content-Type: application/json' \
  -d '{"query":"bank IT tender","texts":["supply of pens","core banking solution"]}'; echo
```

## Wiring the app (on the other machine)

In that machine's `.env`:

```bash
EMBED_BASE_URL=http://10.0.10.11:8080/v1     # the /v1 matters: the client appends /embeddings
EMBED_MODEL=BAAI/bge-small-en-v1.5           # must match the served id
EMBED_BATCH_SIZE=32
EMBED_TIMEOUT_S=30

RERANK_BASE_URL=http://10.0.10.11:8081       # no /v1: the client appends /rerank
RERANK_STYLE=tei
RERANK_MODEL=BAAI/bge-reranker-base
RERANK_TIMEOUT_S=30
```

`EMBED_BASE_URL` being set makes `judge_embedder()` build a `RemoteEmbedder`; the local
model is not loaded at all. `RERANK_BASE_URL` being set builds the `RemoteReranker`.
`EMBED_DEVICE` is then irrelevant.

The category-vector cache is keyed by model name (`cache/lookups.py`), so changing
`EMBED_MODEL` invalidates it safely. Nothing else persists embeddings: passages are
embedded per request, so there is no index to rebuild.

Both call sites are best-effort: if a service is down, the Judge falls back to BM25
words (and keeps the fused order when reranking fails), so a restart here does not
fail a bid.

## Measured on this host (i9-13900K, 6 CPUs per container, cold model loaded)

| Call | Time |
|---|---|
| embed 100 × ~500-token chunks (`bge-small-en-v1.5`) | 2.0–2.6 s |
| rerank 24 passages (`bge-reranker-base`) | ~2.0 s |
| RAM while serving | embed 313 MB, rerank 2.4 GB |
| GPU | untouched: vLLM still holds ~23.7 of 24.5 GB |

Both were run through the app's own `RemoteEmbedder` / `RemoteReranker` classes, so the
wire format is proven, not just the curl.

### Pitfalls that cost a round trip here

- **429 `no permits available`.** TEI takes one concurrency permit per *input*, not per
  request, so `--max-concurrent-requests` must be at least the client batch. The app
  sends up to `EMBED_BATCH_SIZE` (default 64) inputs and up to `rerank_pool` (24) pairs;
  both services run with 128 so two callers in flight still fit. The app logs the
  failure and falls back to BM25, so a 429 degrades quality rather than failing the bid.
- **`--auto-truncate` is required.** Chunks are ~3 000 chars (~750 tokens) and both
  models cap at 512; without the flag TEI rejects them instead of truncating.
- **Docker network pool exhaustion.** This daemon already has 31 networks and
  `all predefined address pools have been fully subnetted`, so the compose file pins
  `10.99.0.0/24` for its own network.
- The containers use the `tei-models` volume, not the host Hugging Face cache, so the
  model is downloaded once on first start (~130 MB embed, ~1.1 GB rerank).

## Swapping in the full-size pair

Set in `deploy/tei/.env` and `docker compose up -d`:

```bash
EMBED_MODEL_ID=BAAI/bge-m3                 # 568M, ~1.1 GB fp16, 1024 dims
RERANK_MODEL_ID=BAAI/bge-reranker-v2-m3    # 568M, ~9 s per 24 passages on CPU
```

Only do this if latency is acceptable; on CPU bge-m3 embeds ~2.5 pages/s and the
reranker costs ~9 s per 24 passages, which is the measured reason the small pair is
the default. The full-size models are already in this host's Hugging Face cache, but
the containers use the `tei-models` volume, so the first start downloads them again.
