#!/usr/bin/env bash
# Goose launcher invoked by the per-user systemd agent.service.
# Reads /etc/agent-platform/goose.env (set by plugin_configure) + EnvironmentFile.

set -euo pipefail

: "${GOOSE_MODE:?GOOSE_MODE required (docker|tarball)}"
: "${OPENAI_BASE_URL:?OPENAI_BASE_URL required}"
GOOSE_MODEL="${GOOSE_MODEL:-qwen-coder-32b}"

case "${GOOSE_MODE}" in
    docker)
        exec docker run --rm \
            --name agent-platform-goose \
            -e OPENAI_BASE_URL \
            -e OPENAI_API_KEY \
            -e GOOSE_MODEL \
            -v "/home/${AGENT_USER}/workspace:/workspace" \
            "${GOOSE_IMAGE}"
        ;;
    tarball)
        export OPENAI_BASE_URL OPENAI_API_KEY GOOSE_MODEL
        cd "/home/${AGENT_USER}/workspace" 2>/dev/null || cd "/home/${AGENT_USER}"
        exec "${GOOSE_FALLBACK}" session
        ;;
    *)
        echo "FATAL: unknown GOOSE_MODE=${GOOSE_MODE}" >&2
        exit 1
        ;;
esac
