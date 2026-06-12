#!/usr/bin/env bash
# DeepSeek-Coder-V2-Lite-Instruct on vLLM (no quant, ~32GB)
set -euo pipefail
PORT="${AGENT_PLATFORM_VLLM_DEEPSEEK_PORT:-8002}"
MODELS_DIR="${AGENT_PLATFORM_MODELS_DIR:-/data/models}"
MODEL_PATH="$MODELS_DIR/deepseek-coder-v2-lite"

if [ ! -d "$MODEL_PATH" ]; then
  huggingface-cli download deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct --local-dir "$MODEL_PATH"
fi

exec vllm serve "$MODEL_PATH" \
  --max-model-len 32768 \
  --port "$PORT" \
  --served-model-name deepseek-coder-v2-lite
