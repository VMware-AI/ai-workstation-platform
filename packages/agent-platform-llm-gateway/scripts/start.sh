#!/usr/bin/env bash
# 前台启动 (dev / debugging 用)；生产用 systemd
set -euo pipefail

CFG="${AGENT_PLATFORM_LLM_GATEWAY_CONFIG:-./config/config.yaml.example}"
PORT="${AGENT_PLATFORM_LLM_GATEWAY_PORT:-4000}"
WORKERS="${AGENT_PLATFORM_LLM_GATEWAY_WORKERS:-2}"

# Prefer the validated CLI entry installed by `uv tool install
# agent-platform-llm-gateway`; fall back to raw litellm so the script keeps
# working from a quick `pip install litellm` setup.
if command -v agent-platform-llm-gateway >/dev/null 2>&1; then
  echo "[start] via agent-platform-llm-gateway start (validated) config=$CFG port=$PORT workers=$WORKERS"
  exec agent-platform-llm-gateway start --config "$CFG" --port "$PORT" --host 0.0.0.0 --num-workers "$WORKERS"
fi

if ! command -v litellm >/dev/null 2>&1; then
  echo "neither agent-platform-llm-gateway nor litellm found." >&2
  echo "  install: uv tool install agent-platform-llm-gateway   # preferred" >&2
  echo "  fallback: pip install 'litellm[proxy]'" >&2
  exit 2
fi

if [ ! -f "$CFG" ]; then
  echo "config not found: $CFG" >&2
  echo "  set AGENT_PLATFORM_LLM_GATEWAY_CONFIG or cp config/config.yaml.example -> your-config.yaml" >&2
  exit 2
fi

echo "[start] via raw litellm (no validation) config=$CFG port=$PORT workers=$WORKERS"
exec litellm --config "$CFG" --port "$PORT" --host 0.0.0.0 --num_workers "$WORKERS"
