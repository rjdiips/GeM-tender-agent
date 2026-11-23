# TEI embed + rerank on the model host

The app runs on another machine and, when pointed here, calls these services over the LAN:

| Service | Container | URL the app uses | Model |
|---|---|---|---|
| embeddings | `gem-tei-embed-1` (or `gem-tei-embed-gpu-1`) | `http://10.0.10.11:8080/v1/embeddings` | `sentence-transformers/all-MiniLM-L6-v2` (384 dims, 256-token window) |
| rerank | `gem-tei-rerank-1` | `http://10.0.10.11:8081/rerank` | `BAAI/bge-reranker-base` |

The embedder is **the same model the app loads locally**, with the same 256-token window,
so remote and local are quality-identical (spec 3.5.1). What changes is where the CPU time is
spent. The reranker is kept for A/B only and should stay unused: it lost top-8 recall on both
slices for +4 s a bid.

## Start / stop

```bash
cd deploy/tei
docker compose up -d          # MiniLM on 6 CPUs (8080) and the optional reranker (8081)
docker compose ps
docker compose logs -f embed
```

GPU instead (when vLLM is stopped or leaves room for it):

```bash
docker compose stop embed            # both bind 8080
docker compose --profile gpu up -d embed-gpu
docker compose stop embed-gpu && docker compose up -d embed   # back to the CPU
```

There is **no auto mode**, and there can't be one in this stack: TEI ships separate CPU and
CUDA images (`cpu-1.9` / `1.9`) and its router has no `--device` flag — the backend is fixed
at build time. Compose likewise cannot pick an image or a GPU reservation from live VRAM.
Choosing automatically would need a wrapper script, a scheduler that supports device
constraints, or a decision in the app; none is in place, so the command you run is the choice.
On 20 Nov the card had ~0.25–0.9 GB free (vLLM at `--gpu-memory-utilization 0.95`), so the
CPU path has been the only usable one.

The first start downloads the ONNX weights into the `gem-tei_tei-models` volume.

This stack is **optional**: local MiniLM measured the same hit@8 (47.4%) at the same window,
so start here only to take the embedding CPU off the app host or to share one embedder
between several app instances. The reranker is not recommended at all.

## Check from this host

```bash
curl -s localhost:8080/info; echo
curl -s localhost:8080/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"sentence-transformers/all-MiniLM-L6-v2","input":["bank IT tender"]}' | head -c 300; echo

curl -s localhost:8081/rerank -H 'Content-Type: application/json' \
  -d '{"query":"bank IT tender","texts":["supply of pens","core banking solution"]}'; echo
```

## Wiring the app (on the other machine)

In that machine's `.env`:

```bash
EMBED_BASE_URL=http://10.0.10.11:8080/v1                 # the /v1 matters: the client appends /embeddings
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2       # must match the served id
EMBED_BATCH_SIZE=64
EMBED_TIMEOUT_S=30

RERANK_BASE_URL=                                         # leave empty: the reranker lost top-8 recall
```

`EMBED_BASE_URL` being set makes `judge_embedder()` build a `RemoteEmbedder`; no local model
is loaded at all. `RERANK_BASE_URL` being set builds the `RemoteReranker` (not recommended).

The category-vector cache is keyed by model name (`cache/lookups.py`), so changing
`EMBED_MODEL` invalidates it safely. Nothing else persists embeddings: passages are embedded
per request, so there is no index to rebuild.

Both call sites are best-effort: if a service is down, the Judge falls back to BM25 words
(and keeps the fused order when reranking fails), so a restart here does not fail a bid.

## Measured on this host (i9-13900K)

| Call | Time |
|---|---|
| embed 200 × ~3 000-char chunks, MiniLM over TEI (6 CPUs) | 1.95 s = **103 chunks/s** |
| a p50 bid (168 chunks) | ≈1.6 s |
| a p95 bid (1 349 chunks) | ≈13 s |
| rerank 24 passages (`bge-reranker-base`) | ~2–4 s |
| RAM while serving | embed ~0.3 GB, rerank ~2.4 GB |
| GPU | untouched: vLLM keeps ~23.7 of 24.5 GB |

MiniLM via TEI is about 3× faster than bge-small was on the same 6 CPUs (17–30 chunks/s),
because the model is 3× smaller and truncates at 256 tokens. It also beats the in-process
PyTorch path measured during the A/B (21 chunks/s on a busy 32-core host).

### Pitfalls that cost a round trip here

- **429 `no permits available`.** TEI takes one concurrency permit per *input*, not per
  request, so `--max-concurrent-requests` must be at least the client batch. The app sends up
  to `EMBED_BATCH_SIZE` (default 64) inputs and up to `rerank_pool` (24) pairs; both services
  run with 128. The app logs the failure and falls back to BM25, so a 429 degrades quality
  rather than failing the bid.
- **`--auto-truncate` is required.** Chunks are ~3 000 chars (~750 tokens) and MiniLM caps at
  256; without the flag TEI rejects them instead of truncating.
- **Docker network pool exhaustion.** This daemon already has 31 networks and
  `all predefined address pools have been fully subnetted`, so the compose file pins
  `10.99.0.0/24` for its own network.
- **`bge-reranker-v2-m3` cannot run on TEI's CPU image**: the repo has no `onnx/model.onnx`
  export, and the backend fails to start. The A/B ran it in-process instead.
- The containers use the `tei-models` volume, not the host Hugging Face cache, so the model is
  downloaded once on first start (~90 MB embed, ~1.1 GB rerank).

## Swapping models

Set in `deploy/tei/.env`, then `docker compose up -d embed` (it recreates the container):

```bash
EMBED_MODEL_ID=BAAI/bge-small-en-v1.5       # ties MiniLM at hit@8, loses at hit@24, 3x slower
EMBED_MODEL_ID=BAAI/bge-m3                  # 568M, 1024 dims, ~2.5 pages/s on CPU
RERANK_MODEL_ID=BAAI/bge-reranker-v2-m3     # in-process only, ~16 s per bid (see above)
```

Only do this if a new A/B (spec 3.5.1, `eval/retrieval_ab/`) says so; the 20 Nov run found no
quality gain over MiniLM and a clear loss from both rerankers.
