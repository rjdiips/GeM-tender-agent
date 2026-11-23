#!/usr/bin/env bash
# Detached A/B matrix for the passage rankers.
#
#   setsid nohup bash eval/retrieval_ab/run_ab.sh >/dev/null 2>&1 &
#
# It survives closing the session/shell. Progress and per-config output:
#   eval/retrieval_ab/logs/run.log        overall progress
#   eval/retrieval_ab/logs/<name>.log     one config's stdout/stderr
#   eval/retrieval_ab/<name>.jsonl        one row per case, written when the config finishes
# Stop it with:  kill -TERM -"$(cat eval/retrieval_ab/logs/run.pid)"
# Re-run only what is missing: the script skips a config whose .jsonl already exists.
set -u

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=eval/retrieval_ab/logs
OUT=eval/retrieval_ab
mkdir -p "$LOG"
echo "$$" > "$LOG/run.pid"
exec >>"$LOG/run.log" 2>&1
echo "== started $(date -Is) pid $$ host $(hostname)"

wait_health() {
  local url=$1 tries=${2:-180}
  for _ in $(seq 1 "$tries"); do
    if [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$url")" = 200 ]; then
      echo "== $url healthy"
      return 0
    fi
    sleep 5
  done
  echo "!! $url never became healthy"
  return 1
}

run() {
  local name=$1; shift
  if [ -s "$OUT/$name.jsonl" ] && [ "${FORCE:-0}" != 1 ]; then
    echo "== skip $name (already done)"
    return 0
  fi
  echo "== $name start $(date -Is)"
  uv run python eval/retrieval_ab.py --name "$name" "$@" >"$LOG/$name.log" 2>&1
  echo "== $name exit $? $(date -Is)"
}

EMBED_URL=http://10.0.10.11:8080/v1
EMBED_MODEL=BAAI/bge-small-en-v1.5
RR_URL=http://10.0.10.11:8081

wait_health "${EMBED_URL%/v1}/health" || exit 1
wait_health "$RR_URL/health" || exit 1

# 1. The full-size reranker, which TEI cannot serve (no ONNX export for bge-reranker-v2-m3),
#    so it runs in-process: embedding vectors are already cached, only the 24 pairs are scored.
run bge-small+rr-m3-relevant --relevant-only \
  --embed-url "$EMBED_URL" --embed-model "$EMBED_MODEL" \
  --rerank-model BAAI/bge-reranker-v2-m3

# 2. The model the app falls back to today (in-process, 256-token window).
run minilm-relevant --relevant-only \
  --embed-model sentence-transformers/all-MiniLM-L6-v2

# 3. The whole labelled set with the deployed embedder, so the negatives are covered too.
run bge-small-full \
  --embed-url "$EMBED_URL" --embed-model "$EMBED_MODEL"

# 4. The same full set with the small reranker, reusing the cached embeddings from 3.
run bge-small+rr-base-full \
  --embed-url "$EMBED_URL" --embed-model "$EMBED_MODEL" \
  --rerank-url "$RR_URL" --rerank-model BAAI/bge-reranker-base

echo "== done $(date -Is)"
