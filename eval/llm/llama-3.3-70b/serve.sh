#!/usr/bin/env bash
# Llama-3.3-70B-Instruct-AWQ on vLLM (~40GB; need 48GB GPU)
set -euo pipefail
PORT="${AGENT_PLATFORM_VLLM_LLAMA_PORT:-8003}"
MODELS_DIR="${AGENT_PLATFORM_MODELS_DIR:-/data/models}"
MODEL_PATH="$MODELS_DIR/llama-3.3-70b-awq"

if [ ! -d "$MODEL_PATH" ]; then
  huggingface-cli download hugging-quants/Meta-Llama-3.3-70B-Instruct-AWQ --local-dir "$MODEL_PATH"
fi

exec vllm serve "$MODEL_PATH" \
  --quantization awq \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.93 \
  --port "$PORT" \
  --served-model-name llama-3.3-70b
