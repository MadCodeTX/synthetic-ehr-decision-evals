#!/bin/bash
# Serve CLM-8B (Contrastive-LM/CLM) behind its TypeSafe-compatible /v1/systemone API, as benchmarked here.
#   encoder: vLLM (docker) Qwen/Qwen3-8B, --runner pooling (last-token), prefix caching, one GPU
#   API    : clm.server from a clone of https://github.com/Contrastive-LM/CLM (run from source)
# Env: CLM_REPO (clone), CLM_PY (python with torch+fastapi+uvicorn+requests), CLM_CKPT (reference head .pt),
#      CLM_FT (optional fine-tuned head .pt), MAXLEN (default 8192), EMB_GPU (0), HEAD_DEVICE (cuda:1), PORT (8700)
set -u
: "${CLM_REPO:?set CLM_REPO}" "${CLM_PY:?set CLM_PY}" "${CLM_CKPT:?set CLM_CKPT}"
MAXLEN="${MAXLEN:-8192}"; EMB_GPU="${EMB_GPU:-0}"; HEAD_DEVICE="${HEAD_DEVICE:-cuda:1}"; PORT="${PORT:-8700}"
docker run -d --name clm-qwen3-8b-emb --gpus "\"device=$EMB_GPU\"" --ipc=host \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" -p 8095:8000 vllm/vllm-openai:v0.30.0 \
  --model Qwen/Qwen3-8B --served-model-name qwen3-8b --runner pooling --enable-prefix-caching \
  --max-model-len "$MAXLEN" --gpu-memory-utilization 0.90 --max-num-seqs 64
for i in $(seq 1 90); do curl -s -m3 localhost:8095/v1/models | grep -q qwen3-8b && break; sleep 5; done
EXTRA=(); [ -n "${CLM_FT:-}" ] && EXTRA=(--model "clm-ft-v1dev=$CLM_FT")
cd "$CLM_REPO" && PYTHONPATH="$CLM_REPO/src" exec "$CLM_PY" -m clm.server --port "$PORT" \
  --emb-url http://127.0.0.1:8095/v1/embeddings --emb-model qwen3-8b --max-tokens "$MAXLEN" \
  --ckpt "$CLM_CKPT" "${EXTRA[@]}" --no-download --no-ui --device "$HEAD_DEVICE"
