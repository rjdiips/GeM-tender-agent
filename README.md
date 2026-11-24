# GeM Tender Agent

## Layout

| Path | Contents |
|---|---|
| `gem_agent/app/` | FastAPI chat backend (SSE) |
| `gem_agent/graph/` | langgraph graph, RunState, checkpointer |
| `gem_agent/agents/` | chat, planner, searcher, collector, judge, reporter, memory step |
| `gem_agent/tools/` | HTTP clients + rate limiter, browser fallback, GeM tools, PDF/OCR |
| `gem_agent/retrieval/` | bge-m3, reranker, BM25, fusion, chunking |
| `gem_agent/index/` | bid cache (`bids.sqlite`) and cards, copy of the old index (`bids migrate`) |
| `gem_agent/cache/` | document, verdict and lookup caches, pre-fetch |
| `gem_agent/harness/` | capabilities, ladder, discovery, skill builder/library, checks, Monty sandbox |
| `gem_agent/memory/` | user memory, saved topic meanings, run logs |
| `gem_agent/jobs/` | retention clean-up, scheduled searches |
| `web/` | React front end (Vite) |
| `skills/` | saved skills + records |
| `knowledge/` | `users/<user>/memory.json`, `topics/` |
| `data/` | `bids/bids.sqlite`, `cache/`, `runs/` (not in git) |
| `eval/` | labelled set and evaluation (spec 13) |

## Live search

Tenders are found by searching GeM live, in two stages (spec 6.2). The first page of every search is read and judged first, then the rest. Base searches for bank IT and consultancy bids always run, and so do the Planner's terms, categories and buyers. The bids the Searcher shortlists are kept in a small bid cache (`data/bids/bids.sqlite`, pruned 30 days after they close), and base search results for one hour. No index is kept up to date in the background, so there are no timers or jobs to install. To bring the cards of an old index across, run `uv run gem-agent bids migrate`.

## Setup

    uv sync
    uv run playwright install chromium
    cp .env.example .env
    cd web && npm install && npm run build

## Run

Two machines, two jobs. The **model host (10.0.10.11) is fixed** and is named in the app host's `.env`;
the **app host may change**, and a new one needs no change on the model host.

| Host | Runs | Address |
|---|---|---|
| model host | vLLM, and the optional embedder from `deploy/tei/` | 10.0.10.11, ports 1234 and 8080 |
| app host | the web chat and all GeM traffic | any LAN address, port 8700 |

### Model host (10.0.10.11)

vLLM is started outside this repository; only its address matters here (spec 3.5):

```bash
vllm serve . --max-model-len 32768 --max-num-seqs 6 --gpu-memory-utilization 0.95
```

Embeddings are optional. They move the Judge's CPU work to this machine's 6 cores and change no quality:

```bash
cd deploy/tei
docker compose up -d      # MiniLM on 8080; the reranker stays off
docker compose ps
```

The stack binds `0.0.0.0`, so any app host on the LAN reaches it. See `deploy/tei/README.md`.

### App host

Install as in [Setup](#setup), then name the model host. These three lines are the whole model config on
this side:

```bash
LLM_BASE_URL=http://10.0.10.11:1234/v1          # always the model host
EMBED_BASE_URL=http://10.0.10.11:8080/v1        # empty = embed locally with MiniLM instead
RERANK_BASE_URL=                                # keep empty: reranking lost top-8 recall
```

`EMBED_MODEL` must equal the id TEI serves (`sentence-transformers/all-MiniLM-L6-v2`). An empty
`EMBED_BASE_URL` loads MiniLM on the app host and needs no TEI at all. The settings are in
[Remote models](#remote-models).

Start it:

```bash
uv run gem-agent serve          # http://<app host>:8700, serves web/dist
```

`APP_HOST` is the address uvicorn binds and prints. Set it to this machine's LAN address (e.g.
`10.0.8.123`) so the line it logs is the URL users open. The default `0.0.0.0` binds every interface
but prints an address a browser rejects.

For hot reload, run the backend in one terminal and Vite in another; Vite proxies `/api` to `8700`
on the same machine:

```bash
uv run gem-agent serve --port 8700
cd web && npm run dev           # http://localhost:5173
```

On Windows, allow inbound 8700 once, as Administrator, or other machines cannot open the chat:

```powershell
New-NetFirewallRule -DisplayName "GeM tender agent 8700" -Direction Inbound -Protocol TCP -LocalPort 8700 -Action Allow
```

Cached bid documents and verdicts are pruned on demand with `uv run gem-agent retention` (add `--dry-run` to
see the numbers first, `--max-bids N` to cap one pass; default 2000). Documents of a bid no run found relevant go
30 days after it closes (`RETAIN_QUARANTINE_DAYS`), documents of a relevant bid 180 days (`RETAIN_KEPT_AFTER_CLOSE_DAYS`),
verdicts 30 days (`RETAIN_VERDICT_AFTER_CLOSE_DAYS`). A bid in a running run or in the labelled set is never touched.
Run it by hand, or from cron at most hourly, and check `/proc/pressure/io` first: a pass reads every run and deletes
one bid at a time, so do not start it while the disk is already busy.

## Remote models

The LLM already lives behind `LLM_BASE_URL`. Embeddings and reranking can also run on another machine:

| Setting | Meaning |
|---|---|
| `EMBED_BASE_URL` | OpenAI-compatible base, including the version, e.g. `http://10.0.10.11:8080/v1` |
| `EMBED_MODEL` | Model id the server serves, `sentence-transformers/all-MiniLM-L6-v2` |
| `RERANK_BASE_URL` | Base of the reranker; the client posts to `<base>/rerank` |
| `RERANK_MODEL` | Model id the reranker serves, `BAAI/bge-reranker-v2-m3` |
| `RERANK_STYLE` | `tei` (`{query, texts}`) or `cohere` (`{model, query, documents, top_n}`) |

Leave either URL empty to keep the local model. Without `EMBED_BASE_URL` the Judge ranks pages with
all-MiniLM-L6-v2 in-process, and without `RERANK_BASE_URL` there is no reranker — the setup measured
better than bge-small/bge-m3 and both rerankers on 20 Nov (spec 3.5.1, `eval/retrieval_ab/`). A server that
is down does not fail a bid: the Judge falls back to word search, and to the fused order when reranking
fails. See `.env.example` for the timeouts.

On Windows, install Tesseract (UB Mannheim build), add it to `PATH` or set `TESSERACT_CMD`, and keep the
repository out of OneDrive. The file locks and UTF-8 reads/writes are cross-platform.
