#!/usr/bin/env bash
# Qwen2.5-Coder-32B-Instruct-AWQ on vLLM
# Requires: GPU ≥ 24GB (AWQ 量化 ~19GB)
set -euo pipefail
PORT="${AGENT_PLATFORM_VLLM_QWEN_PORT:-8001}"
MODELS_DIR="${AGENT_PLATFORM_MODELS_DIR:-/data/models}"
MODEL_PATH="$MODELS_DIR/qwen-coder-32b-awq"

if [ ! -d "$MODEL_PATH" ]; then
  echo "[INFO] downloading model to $MODEL_PATH ..."
  huggingface-cli download Qwen/Qwen2.5-Coder-32B-Instruct-AWQ --local-dir "$MODEL_PATH"
fi

exec vllm serve "$MODEL_PATH" \
  --quantization awq \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.92 \
  --port "$PORT" \
  --served-model-name qwen-coder-32b
