#!/usr/bin/env bash
# Start the embedder on the GPU when the card has room, else on the CPU.
#
#   ./up.sh          # auto: GPU if TEI_GPU_MIN_FREE_MB is free, else CPU
#   ./up.sh cpu      # force CPU
#   ./up.sh gpu      # force GPU (fails loudly if the card has no room)
#
# Both variants serve the same model on the same port, so the app's EMBED_BASE_URL never
# changes. Only one of gem-tei-embed-1 / gem-tei-embed-gpu-1 may run: they share port 8080.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

mode=${1:-auto}
need=${TEI_GPU_MIN_FREE_MB:-2048}

free_mb() {
  nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '
}

have_nvidia_runtime() {
  docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'
}

free=$(free_mb || true)
if [ "$mode" = status ]; then
  docker compose ps embed embed-gpu
  exit 0
fi
if [ "$mode" = auto ]; then
  if [ -n "${free:-}" ] && [ "$free" -ge "$need" ] && have_nvidia_runtime; then
    mode=gpu
  else
    mode=cpu
  fi
fi

case "$mode" in
  gpu)
    if [ -z "${free:-}" ] || [ "$free" -lt "$need" ]; then
      echo "GPU free ${free:-unknown} MiB < ${need} MiB (TEI_GPU_MIN_FREE_MB); not starting on the GPU" >&2
      exit 2
    fi
    if ! have_nvidia_runtime; then
      echo "the docker daemon has no nvidia runtime; not starting on the GPU" >&2
      exit 2
    fi
    echo "embedder: GPU (${free} MiB free >= ${need} MiB)"
    docker compose stop embed >/dev/null 2>&1 || true
    docker compose --profile gpu up -d embed-gpu
    ;;
  cpu)
    echo "embedder: CPU (GPU free: ${free:-unknown} MiB, need ${need} MiB for the GPU)"
    docker compose --profile gpu stop embed-gpu >/dev/null 2>&1 || true
    docker compose up -d embed
    ;;
  *)
    echo "usage: $0 [auto|cpu|gpu]" >&2
    exit 64
    ;;
esac

docker compose ps embed embed-gpu 2>/dev/null || docker compose ps
