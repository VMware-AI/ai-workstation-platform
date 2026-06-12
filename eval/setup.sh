#!/usr/bin/env bash
# M0.2 评测环境初始化 — 在 GPU 服务器或评测节点上跑
#
# 用途：
#   1. 创建 eval 专用 Python venv (uv)
#   2. 安装 vLLM / LiteLLM / openai client / pyyaml / pytest 等依赖
#   3. 检测 GPU
#   4. 提示下一步（按 #2/#3/#4 装 LLM、按 #5 起网关、按 #6 装 agent）
#
# 不做：
#   - 下载模型权重（按需，单独脚本）
#   - 启 vLLM 服务（用 eval/llm/*/serve.sh）
#   - 装 agent（用 eval/agents/install-all.sh）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---- helpers ----
log()  { echo "[$(date +%H:%M:%S)] $*"; }
warn() { echo "[$(date +%H:%M:%S)] WARN: $*" >&2; }
die()  { echo "[$(date +%H:%M:%S)] FATAL: $*" >&2; exit 1; }

# ---- 0. preflight ----
log "0/5 preflight checks"
command -v uv >/dev/null 2>&1 || die "uv not installed — see https://docs.astral.sh/uv/"
command -v python3 >/dev/null 2>&1 || die "python3 not installed"

if [ ! -f .env ]; then
  log "    no .env yet — copying from .env.example"
  cp .env.example .env
  warn "edit eval/.env to set real values before running runner"
fi

# ---- 1. venv ----
log "1/5 create venv (.venv) with Python 3.11"
uv venv .venv --python 3.11 --quiet
# shellcheck source=/dev/null
source .venv/bin/activate

# ---- 2. install eval deps ----
log "2/5 install eval deps (pyyaml + openai + pytest + pytest-cov)"
uv pip install --quiet pyyaml openai pytest pytest-cov

# ---- 3. install vLLM (optional, only if --vllm) ----
if [ "${1:-}" = "--vllm" ]; then
  log "3/5 install vLLM (large, ~5 min)"
  uv pip install --quiet vllm
else
  log "3/5 skip vLLM (pass --vllm to install; needs GPU/CUDA host)"
fi

# ---- 4. install LiteLLM (optional, only if --litellm) ----
if [ "${1:-}" = "--litellm" ] || [ "${2:-}" = "--litellm" ]; then
  log "4/5 install LiteLLM proxy"
  uv pip install --quiet 'litellm[proxy]'
else
  log "4/5 skip LiteLLM (pass --litellm to install)"
fi

# ---- 5. GPU detection ----
log "5/5 GPU detection"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
else
  warn "no nvidia-smi — runner can still test agents that talk to a remote gateway, but cannot host LLM here"
fi

echo
echo "=== setup done ==="
echo "next steps:"
echo "  - bash eval/llm/qwen-coder-32b/serve.sh   (start LLM-A on port \$AGENT_PLATFORM_VLLM_QWEN_PORT)"
echo "  - bash eval/gateway/start.sh              (start LiteLLM on port 4000)"
echo "  - bash eval/agents/install-all.sh         (install qcoder/Goose/Claude Code)"
echo "  - uv run python eval/runner.py --dry-run  (smoke test)"
