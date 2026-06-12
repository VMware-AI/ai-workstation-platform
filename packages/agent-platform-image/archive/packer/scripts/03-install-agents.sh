#!/usr/bin/env bash
# =============================================================================
# 03-install-agents.sh
# Install AI coding agents: qcoder / Goose / Claude Code (optional).
# Controlled by environment variables:
#   AGENT_INSTALL_ENABLED   (default: true)
#   AGENT_CC_VERSION        Claude Code version (default: latest)
#   AGENT_GOOSE_VERSION     Goose version (default: latest)
# =============================================================================
set -euo pipefail

echo "[03] Installing AI coding agents..."

export DEBIAN_FRONTEND=noninteractive
export AGENT_INSTALL_ENABLED="${AGENT_INSTALL_ENABLED:-true}"
export AGENT_CC_VERSION="${AGENT_CC_VERSION:-}"
export AGENT_GOOSE_VERSION="${AGENT_GOOSE_VERSION:-}"
export AGENT_QCODER_VERSION="${AGENT_QCODER_VERSION:-}"

if [[ "$AGENT_INSTALL_ENABLED" != "true" ]]; then
  echo "[03] AGENT_INSTALL_ENABLED=false — skipping agent installation."
  exit 0
fi

# ── Claude Code ───────────────────────────────────────────────────────────────
install_claude_code() {
  echo "[03] Installing Claude Code..."
  if command -v cc &>/dev/null || command -v claude &>/dev/null; then
    echo "[03] Claude Code already installed."
    return
  fi

  # Official installer (requires npm)
  npm install -g @anthropic-ai/claude-code 2>&1 | tail -3 || {
    echo "[03] WARNING: npm install failed, trying curl installer..."
    # Fallback: curl install script
    curl -fsSL https://download.claude.com/claude-code/installer.sh -o /tmp/cc-install.sh
    sh /tmp/cc-install.sh 2>&1 | tail -5
    rm /tmp/cc-install.sh
  }

  # Smoke test
  cc --version 2>/dev/null || claude --version 2>/dev/null || {
    echo "[03] WARNING: Claude Code install completed but binary not found in PATH"
  }
}

# ── Goose ──────────────────────────────────────────────────────────────────────
install_goose() {
  echo "[03] Installing Goose..."
  if command -v goose &>/dev/null; then
    echo "[03] Goose already installed."
    return
  fi

  # Build from source or download binary
  _goose_ver="${AGENT_GOOSE_VERSION:-v1.2.0}"
  curl -fsSL "https://github.com/goose-stack/goose/releases/download/${_goose_ver}/goose-linux-amd64" \
    -o /usr/local/bin/goose
  chmod +x /usr/local/bin/goose

  goose --version 2>/dev/null || echo "[03] WARNING: goose --version failed"
}

# ── qcoder ─────────────────────────────────────────────────────────────────────
install_qcoder() {
  echo "[03] Installing qcoder..."
  if command -v qcoder &>/dev/null; then
    echo "[03] qcoder already installed."
    return
  fi

  curl -fsSL https://qjishen.com/qcoder/install.sh -o /tmp/qcoder-install.sh 2>/dev/null \
    && sh /tmp/qcoder-install.sh 2>&1 | tail -5 \
    || echo "[03] WARNING: qcoder install script not available, skipping"

  rm -f /tmp/qcoder-install.sh
}

# ── Install all agents ──────────────────────────────────────────────────────────
install_claude_code
install_goose
install_qcoder

# ── Agent smoke tests ──────────────────────────────────────────────────────────
echo "[03] Agent smoke tests:"
cc --version 2>/dev/null || echo "  cc: not found (OK if not licensed)"
goose --version 2>/dev/null || echo "  goose: not found"
qcoder --version 2>/dev/null || echo "  qcoder: not found"

echo "[03] Agent installation complete."
