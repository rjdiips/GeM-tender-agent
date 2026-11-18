# GeM Tender Agent

## Layout

| Path | Contents |
|---|---|
| `gem_agent/app/` | FastAPI chat backend (SSE) |
| `gem_agent/graph/` | langgraph graph, RunState, checkpointer |
| `gem_agent/agents/` | chat, planner, searcher, collector, judge, reporter, memory step |
| `gem_agent/tools/` | HTTP clients + rate limiter, browser fallback, GeM tools, PDF/OCR |
| `gem_agent/retrieval/` | bge-m3, reranker, BM25, fusion, chunking |
| `gem_agent/index/` | local open-bid index: sync, query, probe |
| `gem_agent/cache/` | document, verdict and lookup caches, pre-fetch |
| `gem_agent/harness/` | capabilities, ladder, discovery, skill builder/library, checks, Monty sandbox |
| `gem_agent/memory/` | user memory, saved topic meanings, run logs |
| `gem_agent/jobs/` | retention clean-up, scheduled searches |
| `web/` | React front end (Vite) |
| `skills/` | saved skills + records |
| `knowledge/` | `users/<user>/memory.json`, `topics/` |
| `data/` | `index/bids.sqlite`, `cache/`, `runs/` (not in git) |
| `deploy/systemd/` | index sync and nightly job timers |
| `eval/` | labelled set and evaluation (spec 13) |

## Setup

    uv sync
    uv run playwright install chromium
    cp .env.example .env
    cd web && npm install
