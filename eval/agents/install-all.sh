#!/usr/bin/env bash
# 一键装三个 agent（开发本机或评测节点用）
# 不在 CI 跑（需要 npm + 网络）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG=/tmp/agent-platform-agent-install-$(date +%s).log

require() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 2; }; }
require node
require npm

REGISTRY="${AGENT_PLATFORM_NPM_REGISTRY:-https://registry.npmjs.org}"
NPM_FLAGS=(--global --registry "$REGISTRY")

echo "[1/3] qcoder (Qwen Code)..."
npm install "${NPM_FLAGS[@]}" @qwen-code/qwen-code | tee -a "$LOG"

echo "[2/3] Goose..."
if [ "${AGENT_PLATFORM_OFFLINE:-}" = "1" ]; then
  echo "  OFFLINE mode — skip download, expect ${GOOSE_OFFLINE_PATH:?'set GOOSE_OFFLINE_PATH'} on PATH"
else
  # Pinned + sha256-verified (#248) — same release as the cloud-init path
  # (cloudinit/agents.ts) and the C3 image; bump all three together.
  # Upstream repo moved block/goose → aaif-goose/goose (old URLs redirect).
  GOOSE_VERSION=v1.37.0
  GOOSE_SHA256=d575073c059f22e0b697f1a0b502ecf629cfd4604a67e7659a8542a4ea7463bc
  curl -fsSL -o /tmp/goose.tar.gz \
    "https://github.com/aaif-goose/goose/releases/download/${GOOSE_VERSION}/goose-x86_64-unknown-linux-gnu.tar.gz"
  echo "${GOOSE_SHA256}  /tmp/goose.tar.gz" | sha256sum -c - | tee -a "$LOG"
  BIN_DIR=/usr/local/bin
  [ -w "$BIN_DIR" ] || BIN_DIR="$HOME/.local/bin"
  mkdir -p "$BIN_DIR"
  tar -xzf /tmp/goose.tar.gz -C /tmp ./goose
  install -m 755 /tmp/goose "$BIN_DIR/goose"
  rm -f /tmp/goose.tar.gz /tmp/goose
  echo "  goose ${GOOSE_VERSION} -> ${BIN_DIR}/goose" | tee -a "$LOG"
fi

echo "[3/3] Claude Code..."
npm install "${NPM_FLAGS[@]}" @anthropic-ai/claude-code | tee -a "$LOG"

echo
echo "=== 验证 ==="
qwen --version  || echo "qcoder NOT ready"
goose --version || echo "goose NOT ready"
claude --version|| echo "claude-code NOT ready"

echo
echo "Log: $LOG"
