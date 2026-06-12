#!/usr/bin/env bash
# Xiaoguai (小怪) agent plugin (decision 2D).
#
# Contract: see packages/agent-platform-image/docs/plugin-interface.md — exposes
# four functions (install / configure / start / healthcheck), inputs from env
# vars sourced from /etc/agent-platform/install.env.
#
# Install = pip (tracking issue #277 decision: PyPI, same as the C21 deploy
# runcmd). Runtime = `xiaoguai serve` as a per-user systemd daemon (its own web
# UI on :7600). Verified upstream: `serve` runs sqlx migrate on first boot and
# auto-creates ~/.xiaoguai/data.db; health is GET /healthz → ok; the OpenAI-compat
# client reads OPENAI_BASE_URL (with /v1) + OPENAI_API_KEY.
#
# Idempotent: re-running on an already-configured VM is safe.

set -euo pipefail

XIAOGUAI_BIN=""
# Upstream `xiaoguai serve` binds :7600 (not configurable via flag), so keep
# this fixed — an env override would just desync the healthcheck from serve.
readonly XIAOGUAI_PORT=7600

_xg_log() { echo "[xiaoguai-plugin] $*"; }

# --------------------------------------------------------------- 1/4 install

plugin_install() {
    # The golden image bakes python3 but not always the pip module (it ships in
    # the separate python3-pip apt package) — provision it if absent.
    if ! python3 -m pip --version >/dev/null 2>&1; then
        _xg_log "pip module missing; installing python3-pip"
        apt-get update && apt-get install -y python3-pip
    fi

    _xg_log "pip install xiaoguai==${AGENT_VERSION}"
    # --break-system-packages is a no-op on 22.04 (no PEP 668 marker) and
    # future-proofs a 24.04 image bump.
    python3 -m pip install --no-cache-dir --break-system-packages "xiaoguai==${AGENT_VERSION}"

    XIAOGUAI_BIN="$(command -v xiaoguai || true)"
    if [[ -z "${XIAOGUAI_BIN}" ]]; then
        echo "[xiaoguai-plugin] FATAL: 'xiaoguai' not on PATH after pip install" >&2
        return 1
    fi
    _xg_log "installed at ${XIAOGUAI_BIN}"
}

# ------------------------------------------------------------- 2/4 configure

plugin_configure() {
    install -d -m 0755 /etc/agent-platform
    umask 077
    # xiaoguai's OpenAI-compat client expects the /v1 suffix on the base URL
    # (same as the C20 adapter). GATEWAY_URL has no trailing slash by convention,
    # but strip one defensively.
    local base="${GATEWAY_URL%/}/v1"
    cat > "${AGENT_RUNTIME_ENV}" <<EOF
OPENAI_BASE_URL=${base}
OPENAI_MODEL=${AGENT_MODEL:-qwen-coder-32b}
HEARTBEAT_URL=${HEARTBEAT_URL}
AGENT_USER=${AGENT_USER}
EOF
    # OPENAI_API_KEY (LLM gateway key) is appended later by the token-exchange
    # step, once the secret payload returns — same pattern as the goose plugin.
    chmod 0600 "${AGENT_RUNTIME_ENV}"
}

# ----------------------------------------------------------------- 3/4 start

plugin_start() {
    local unit_dir="/home/${AGENT_USER}/.config/systemd/user"
    sudo -u "${AGENT_USER}" mkdir -p "${unit_dir}"

    # `xiaoguai serve` is a plain pip-installed binary — no docker/tarball
    # dispatch, so ExecStart runs it directly (unlike goose's launcher).
    sudo -u "${AGENT_USER}" tee "${unit_dir}/agent.service" >/dev/null <<EOF
[Unit]
Description=Xiaoguai Agent (Agent Platform)
After=network-online.target

[Service]
EnvironmentFile=${AGENT_RUNTIME_ENV}
ExecStart=${XIAOGUAI_BIN} serve
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
EOF

    local user_runtime="/run/user/${AGENT_USER_UID}"
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
        systemctl --user daemon-reload
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
        systemctl --user enable --now agent.service
}

# ----------------------------------------------------------- 4/4 healthcheck

plugin_healthcheck() {
    local user_runtime="/run/user/${AGENT_USER_UID}"
    # First boot runs sqlx migrate before the server listens, so allow longer
    # than goose's 30s.
    local timeout=60
    local elapsed=0

    while (( elapsed < timeout )); do
        if curl -sf "http://localhost:${XIAOGUAI_PORT}/healthz" 2>/dev/null | grep -qw ok; then
            _xg_log "xiaoguai /healthz ok after ${elapsed}s"
            return 0
        fi
        sleep 2
        ((elapsed += 2))
    done

    echo "[xiaoguai-plugin] FATAL: /healthz not ok within ${timeout}s" >&2
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
        systemctl --user status agent.service >&2 || true
    return 1
}
