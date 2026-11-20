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
    cd web && npm install

## Remote models

The LLM already lives behind `LLM_BASE_URL`. Embeddings and reranking can also run on another machine:

| Setting | Meaning |
|---|---|
| `EMBED_BASE_URL` | OpenAI-compatible base, including the version, e.g. `http://10.0.10.11:8080/v1` |
| `EMBED_MODEL` | Model id the server serves, `BAAI/bge-m3` |
| `RERANK_BASE_URL` | Base of the reranker; the client posts to `<base>/rerank` |
| `RERANK_MODEL` | Model id the reranker serves, `BAAI/bge-reranker-v2-m3` |
| `RERANK_STYLE` | `tei` (`{query, texts}`) or `cohere` (`{model, query, documents, top_n}`) |

Leave either URL empty to keep the local model. A server that is down does not fail a bid: the Judge falls
back to word search, and to the fused order when reranking fails. See `.env.example` for the timeouts.

On Windows, install Tesseract (UB Mannheim build), add it to `PATH` or set `TESSERACT_CMD`, and keep the
repository out of OneDrive. The file locks and UTF-8 reads/writes are cross-platform.
