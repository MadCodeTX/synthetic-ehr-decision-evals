#!/bin/bash
# Serve Julia-1 (or a fine-tuned Julia checkpoint) as benchmarked here: serve_julia.py inside a Docker container
# with NO network (--network none), the model and code mounted read-only, listening on a unix socket in a
# host directory; socat bridges 127.0.0.1:$PORT to that socket for the harness.
#
# Env:
#   JULIA_RELEASE  resolved (no symlinks) copy of the SupersonicLabs/Julia-1 snapshot   [required]
#   JULIA_PYDEPS   pip --target dir holding transformers==5.0.0 (+deps, minus numpy)     [required]
#   JULIA_CKPT     optional fine-tuned checkpoint dir (train_julia.py --out .../best); default: the release
#   NAME           served model name and container suffix (default julia-1)
#   GPU            GPU index, or "cpu" (default 0)
#   PORT           host TCP port for the socat bridge (default 8098)
#   SOCK_DIR       host dir for the socket (default ./sock)
#   IMAGE          image with python3 + torch (default vllm/vllm-openai:v0.30.0: torch 2.13, CUDA 13)
#   HEAD_LENGTH    question+options budget (default 640)
set -euo pipefail
: "${JULIA_RELEASE:?set JULIA_RELEASE}" "${JULIA_PYDEPS:?set JULIA_PYDEPS}"
NAME="${NAME:-julia-1}"; GPU="${GPU:-0}"; PORT="${PORT:-8098}"; HEAD_LENGTH="${HEAD_LENGTH:-640}"
IMAGE="${IMAGE:-vllm/vllm-openai:v0.30.0}"; SOCK_DIR="$(realpath -m "${SOCK_DIR:-./sock}")"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SOCK_DIR"; rm -f "$SOCK_DIR/$NAME.sock"
CKPT_ARGS=(-e JULIA_MODEL_DIR=/release)
[ -n "${JULIA_CKPT:-}" ] && CKPT_ARGS=(-v "$(realpath "$JULIA_CKPT"):/ckpt:ro" -e JULIA_MODEL_DIR=/ckpt)
if [ "$GPU" = cpu ]; then DEV=(-e JULIA_DEVICE=cpu -e JULIA_CPU_THREADS="${CPU_THREADS:-8}" --cpus "${CPU_THREADS:-8}")
else DEV=(--gpus "device=$GPU" -e JULIA_DEVICE=cuda); fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e PYTHONPATH=/pydeps -e JULIA_CODE_DIR=/release \
  -e JULIA_MODEL_NAME="$NAME" -e JULIA_HEAD_LENGTH="$HEAD_LENGTH" -e JULIA_UDS="/sock/$NAME.sock" \
  -v "$(realpath "$JULIA_RELEASE"):/release:ro" -v "$(realpath "$JULIA_PYDEPS"):/pydeps:ro" \
  -v "$HERE:/svc:ro" -v "$SOCK_DIR:/sock" "${CKPT_ARGS[@]}" "${DEV[@]}" \
  --entrypoint python3 "$IMAGE" /svc/serve_julia.py
for _ in $(seq 1 60); do [ -S "$SOCK_DIR/$NAME.sock" ] && break; sleep 1; done
[ -S "$SOCK_DIR/$NAME.sock" ] || { docker logs "$NAME" | tail -20; exit 1; }
PIDF="$SOCK_DIR/socat-$PORT.pid"
[ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null || true
nohup socat "TCP-LISTEN:$PORT,bind=127.0.0.1,reuseaddr,fork" "UNIX-CONNECT:$SOCK_DIR/$NAME.sock" \
  > "$SOCK_DIR/socat-$PORT.log" 2>&1 &
echo $! > "$PIDF"
sleep 1
curl -s -m 10 "http://127.0.0.1:$PORT/health"; echo
