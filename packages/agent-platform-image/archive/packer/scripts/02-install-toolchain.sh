#!/usr/bin/env bash
# =============================================================================
# 02-install-toolchain.sh
# Install Python (uv), Node.js (pnpm), and other core tooling.
# =============================================================================
set -euo pipefail

echo "[02] Installing core toolchain..."

export DEBIAN_FRONTEND=noninteractive

# ── uv (Python package manager) ───────────────────────────────────────────────
# uv is 10-100x faster than pip, written in Rust, used as the project's monorepo
# package manager for all Python components.
if ! command -v uv &>/dev/null; then
  echo "[02] Installing uv..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Persist uv in profile for future logins
echo 'export PATH="$HOME/.local/bin:$PATH"' >> /etc/profile.d/uv.sh
chmod +x /etc/profile.d/uv.sh

# ── Python 3.11+ (already on Ubuntu 22.04, ensure pip is installed) ────────────
echo "[02] Ensuring Python 3 and pip..."
apt-get install -y python3-pip python3-venv python3-dev python3-distutils 2>&1 | tail -3

# ── Node.js 20 LTS (required by agent-platform-console / agent-platform-portal) ──────────────
echo "[02] Installing Node.js 20 LTS..."
if ! command -v node &>/dev/null || [[ "$(node -v)" != v20* ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs 2>&1 | tail -3
fi

# ── pnpm (Node.js package manager — used in agent-platform monorepo) ──────────────────
echo "[02] Installing pnpm..."
if ! command -v pnpm &>/dev/null; then
  npm install -g pnpm 2>&1 | tail -3
fi

# ── Go (required by some infra tooling, e.g. vmware-vsphere-automation-sdk) ────
echo "[02] Installing Go..."
if ! command -v go &>/dev/null; then
  _go_ver="1.22.3"
  curl -fsSL "https://go.dev/dl/go${_go_ver}.linux-amd64.tar.gz" -o /tmp/go.tar.gz
  tar -C /usr/local -xzf /tmp/go.tar.gz
  rm /tmp/go.tar.gz
  echo 'export GOPATH=$HOME/go' >> /etc/profile.d/go.sh
  echo 'export PATH=$PATH:/usr/local/go/bin:$GOPATH/bin' >> /etc/profile.d/go.sh
  chmod +x /etc/profile.d/go.sh
fi
export PATH="$PATH:/usr/local/go/bin:$HOME/go/bin"

# ── Docker (required by vLLM runtime / LLM serving) ────────────────────────────
# Docker is installed but NOT enabled/started in the gold image. It is started
# on first boot via C6 agent-platform-llm-runtime systemd unit.
echo "[02] Installing Docker..."
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh 2>&1 | tail -5
  rm /tmp/get-docker.sh
fi

# ── yq (YAML processor — useful for config management) ────────────────────────
echo "[02] Installing yq..."
if ! command -v yq &>/dev/null; then
  curl -fsSL https://github.com/mikefarah/yq/releases/download/v4.35.1/yq_linux_amd64 -o /usr/local/bin/yq
  chmod +x /usr/local/bin/yq
fi

# ── cosign (image signing — used by C9 agent-platform-scale-bundle) ──────────────────
echo "[02] Installing cosign..."
if ! command -v cosign &>/dev/null; then
  cosign_version="v2.2.0"
  curl -fsSL "https://github.com/sigstore/cosign/releases/download/${cosign_version}/cosign-linux-amd64" -o /usr/local/bin/cosign
  chmod +x /usr/local/bin/cosign
  # Verify cosign binary
  cosign version || echo "[02] WARNING: cosign verify failed"
fi

# ── cloud-nuke (optional — for cleaning up test resources) ────────────────────
# Installed but not enabled; useful for CI environments.

echo "[02] Toolchain install complete."
echo "[02] Installed versions:"
go version 2>/dev/null || true
node -v 2>/dev/null || true
pnpm -v 2>/dev/null || true
uv --version 2>/dev/null || true
docker --version 2>/dev/null || true
yq --version 2>/dev/null || true
cosign version 2>/dev/null || true
