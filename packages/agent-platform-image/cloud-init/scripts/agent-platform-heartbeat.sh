#!/usr/bin/env bash
# Heartbeat stub — invoked by agent-platform-heartbeat.timer every 60s.
#
# Full implementation lands in the monitoring PR (#4/4). This stub:
#   - sources the agent runtime env (AGENT_RUNTIME_ENV, default goose.env)
#   - emits a single JSON line to stderr (visible via journalctl)
#   - POSTs to HEARTBEAT_URL if set, swallowing errors
#
# Designed to never fail the timer; transient network errors are not fatal.

set -uo pipefail

# Runtime env file + agent service name are parameterized so non-goose agents
# (AGENT_KIND != goose) still get a working heartbeat. install.env supplies
# AGENT_RUNTIME_ENV; the service name defaults to ${AGENT_HEARTBEAT_SERVICE} or
# goose-agent.service for back-compat.
AGENT_RUNTIME_ENV="${AGENT_RUNTIME_ENV:-/etc/agent-platform/goose.env}"
set -a
# shellcheck disable=SC1090,SC1091
source "${AGENT_RUNTIME_ENV}" 2>/dev/null || true
set +a

agent_service="${AGENT_HEARTBEAT_SERVICE:-goose-agent.service}"

hostname=$(hostname)
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
agent_active="unknown"

if command -v systemctl >/dev/null 2>&1; then
    agent_active=$(systemctl is-active "${agent_service}" 2>/dev/null || echo "inactive")
fi

payload=$(printf '{"ts":"%s","host":"%s","mode":"%s","goose_active":"%s"}' \
    "${ts}" "${hostname}" "${GOOSE_MODE:-unknown}" "${agent_active}")

echo "${payload}" >&2

if [[ -n "${HEARTBEAT_URL:-}" ]]; then
    curl -fsSL --max-time 5 \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${AGENT_PLATFORM_USER_TOKEN:-}" \
        -X POST -d "${payload}" \
        "${HEARTBEAT_URL}" >/dev/null 2>&1 || true
fi

exit 0
