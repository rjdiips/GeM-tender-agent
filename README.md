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

Tenders are found by searching GeM live, in two stages (spec 6.2). The first page of every search is read and judged first, then the rest. Base searches for bank IT and consultancy bids always run, and so do the Planner's terms, categories and buyers. The bids a search returns are kept in a small bid cache (`data/bids/bids.sqlite`, pruned 30 days after they close), and base search results for one hour. No index is kept up to date in the background, so there are no timers or jobs to install. To bring the cards of an old index across, run `uv run gem-agent bids migrate`.

## Setup

    uv sync
    uv run playwright install chromium
    cp .env.example .env
    cd web && npm install
