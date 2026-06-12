#!/usr/bin/env bash
# Goose agent plugin (decision 2D).
#
# Ported from the previous monolithic install-goose.sh. Contract: see
# packages/agent-platform-image/docs/plugin-interface.md — exposes four functions
# (install / configure / start / healthcheck), takes input from environment
# variables sourced from /etc/agent-platform/install.env.
#
# Idempotent: re-running on an already-configured VM is safe.

set -euo pipefail

GOOSE_FALLBACK_BIN=/opt/goose-fallback/goose
GOOSE_IMAGE_REF=""
GOOSE_MODE=""

_goose_log() { echo "[goose-plugin] $*"; }

# --------------------------------------------------------------- 1/4 install

plugin_install() {
    GOOSE_IMAGE_REF="${AGENT_REGISTRY_URL}/goose:${AGENT_VERSION}"

    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        _goose_log "docker reachable; pulling ${GOOSE_IMAGE_REF}"
        if docker pull "${GOOSE_IMAGE_REF}"; then
            GOOSE_MODE="docker"
            return 0
        fi
        _goose_log "docker pull failed; will try tarball fallback"
    else
        _goose_log "docker not available; will try tarball fallback"
    fi

    if [[ -x "${GOOSE_FALLBACK_BIN}" ]]; then
        _goose_log "found tarball fallback at ${GOOSE_FALLBACK_BIN}"
        GOOSE_MODE="tarball"
        return 0
    fi

    echo "[goose-plugin] FATAL: docker pull failed and no tarball at ${GOOSE_FALLBACK_BIN}" >&2
    return 1
}

# ------------------------------------------------------------- 2/4 configure

plugin_configure() {
    # Write the runtime env consumed by the per-user systemd unit. The path
    # comes from install.env (AGENT_RUNTIME_ENV=/etc/agent-platform/goose.env by
    # convention).
    install -d -m 0755 /etc/agent-platform
    umask 077
    cat > "${AGENT_RUNTIME_ENV}" <<EOF
GOOSE_MODE=${GOOSE_MODE}
GOOSE_IMAGE=${GOOSE_IMAGE_REF}
GOOSE_FALLBACK=${GOOSE_FALLBACK_BIN}
GOOSE_MODEL=${AGENT_MODEL:-qwen-coder-32b}
OPENAI_BASE_URL=${GATEWAY_URL}
HEARTBEAT_URL=${HEARTBEAT_URL}
AGENT_USER=${AGENT_USER}
EOF
    # PR-C will append OPENAI_API_KEY (LLM gateway key) here after the
    # token exchange returns the secret payload.
    chmod 0600 "${AGENT_RUNTIME_ENV}"
}

# ----------------------------------------------------------------- 3/4 start

plugin_start() {
    local unit_dir="/home/${AGENT_USER}/.config/systemd/user"
    sudo -u "${AGENT_USER}" mkdir -p "${unit_dir}"

    sudo -u "${AGENT_USER}" tee "${unit_dir}/agent.service" >/dev/null <<EOF
[Unit]
Description=Goose Agent (Agent Platform)
After=network-online.target

[Service]
EnvironmentFile=${AGENT_RUNTIME_ENV}
ExecStart=/usr/local/bin/agent-platform-agent-launch
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
EOF

    # Install the launcher that systemd --user invokes. This script knows
    # how to dispatch between docker and tarball modes.
    install -m 0755 /opt/agent-platform/agent-plugins/goose-launch.sh /usr/local/bin/agent-platform-agent-launch

    # Tell the per-user systemd to reload and start the unit. Need both
    # XDG_RUNTIME_DIR and the bus address so systemctl --user can find its
    # manager (loginctl enable-linger was already done by install-agent.sh).
    local user_runtime="/run/user/${AGENT_USER_UID}"
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
        systemctl --user daemon-reload
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
        systemctl --user enable --now agent.service
}

# ----------------------------------------------------------- 4/4 healthcheck

plugin_healthcheck() {
    local user_runtime="/run/user/${AGENT_USER_UID}"
    local timeout=30
    local elapsed=0

    while (( elapsed < timeout )); do
        if sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
            systemctl --user is-active agent.service >/dev/null 2>&1
        then
            _goose_log "agent.service active after ${elapsed}s"
            return 0
        fi
        sleep 1
        ((elapsed++))
    done

    echo "[goose-plugin] FATAL: agent.service did not become active within ${timeout}s" >&2
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="${user_runtime}" \
        systemctl --user status agent.service >&2 || true
    return 1
}
