#!/usr/bin/env bash
# =============================================================================
# 04-bootstrap-platform.sh
# Pull agent-platform platform packages (C1/C2/C7/C20) from C4 agent-platform-repo on first boot.
# Runs ONLY when AGENT_PLATFORM_REPO_URL is set.
#
# Environment:
#   AGENT_PLATFORM_REPO_URL    Base URL of agent-platform-repo (C4), e.g. https://repo.internal
#   AGENT_PLATFORM_ENV_VERSION Version of agent-platform-env to pull, e.g. v0.1.0
# =============================================================================
set -euo pipefail

echo "[04] Bootstrapping agent-platform..."

export DEBIAN_FRONTEND=noninteractive

_AGENT_PLATFORM_REPO_URL="${AGENT_PLATFORM_REPO_URL:-}"
_AGENT_PLATFORM_ENV_VERSION="${AGENT_PLATFORM_ENV_VERSION:-latest}"

if [[ -z "$_AGENT_PLATFORM_REPO_URL" ]]; then
  echo "[04] AGENT_PLATFORM_REPO_URL not set — skipping platform bootstrap."
  echo "[04] Run this script manually or set AGENT_PLATFORM_REPO_URL to bootstrap."
  exit 0
fi

echo "[04] Pulling agent-platform platform from $_AGENT_PLATFORM_REPO_URL (version: $_AGENT_PLATFORM_ENV_VERSION)..."

# ── Create agent-platform system user ──────────────────────────────────────────────────
id agent-platform &>/dev/null || useradd --system --create-home --shell /bin/bash agent-platform

# ── Clone or update platform packages ─────────────────────────────────────────
SYSTEMD_UNIT_DIR="/etc/systemd/system"
SERVICE_DIR="/opt/agent-platform"

mkdir -p "$SERVICE_DIR"
mkdir -p "$SYSTEMD_UNIT_DIR"

# agent-platform-control (C1)
if [[ -d "/opt/agent-platform/agent-platform-control" ]]; then
  echo "[04] Updating agent-platform-control..."
  git -C /opt/agent-platform/agent-platform-control pull
else
  echo "[04] Cloning agent-platform-control..."
  git -C "$SERVICE_DIR" clone "${_AGENT_PLATFORM_REPO_URL}/agent-platform-control.git"
fi

# agent-platform-agent-adapter (C20)
if [[ -d "/opt/agent-platform/agent-platform-agent-adapter" ]]; then
  echo "[04] Updating agent-platform-agent-adapter..."
  git -C /opt/agent-platform/agent-platform-agent-adapter pull
else
  echo "[04] Cloning agent-platform-agent-adapter..."
  git -C "$SERVICE_DIR" clone "${_AGENT_PLATFORM_REPO_URL}/agent-platform-agent-adapter.git"
fi

# ── Install agent-platform-control systemd unit ────────────────────────────────────────
# TODO: write actual systemd unit file during M1 C8 implementation
cat > "${SYSTEMD_UNIT_DIR}/agent-platform-control.service" << 'EOF'
[Unit]
Description=Agent Platform Control Plane API (C1)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=agent-platform
WorkingDirectory=/opt/agent-platform/agent-platform-control
ExecStart=/opt/agent-platform/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
Environment="PYTHONPATH=/opt/agent-platform/agent-platform-control"

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable agent-platform-control.service
echo "[04] agent-platform-control.service enabled."

echo "[04] Platform bootstrap complete."
echo "[04] To start: sudo systemctl start agent-platform-control"
