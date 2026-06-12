#!/usr/bin/env bash
# Launcher invoked by goose-agent.service.
# Reads /etc/agent-platform/goose.env, exec's docker container or fallback binary.

set -euo pipefail

# shellcheck disable=SC1091
set -a; source /etc/agent-platform/goose.env; set +a

case "${GOOSE_MODE:-}" in
    docker)
        # --rm so a crashed container doesn't pile up; systemd handles restart.
        exec docker run --rm \
            --name goose-agent \
            --network host \
            -e OPENAI_HOST="${LITELLM_GATEWAY_URL}" \
            -e OPENAI_API_KEY="${AGENT_PLATFORM_USER_TOKEN}" \
            -e GOOSE_PROVIDER=openai \
            -e GOOSE_MODEL="${GOOSE_MODEL}" \
            -v "/home/${AGENT_PLATFORM_USER}/workspace:/workspace" \
            "${GOOSE_IMAGE}" \
            session
        ;;
    tarball)
        export OPENAI_HOST="${LITELLM_GATEWAY_URL}"
        export OPENAI_API_KEY="${AGENT_PLATFORM_USER_TOKEN}"
        export GOOSE_PROVIDER=openai
        export GOOSE_MODEL="${GOOSE_MODEL}"
        cd "/home/${AGENT_PLATFORM_USER}/workspace" || cd /
        exec sudo -u "${AGENT_PLATFORM_USER}" --preserve-env=OPENAI_HOST,OPENAI_API_KEY,GOOSE_PROVIDER,GOOSE_MODEL \
            "${GOOSE_FALLBACK}" session
        ;;
    *)
        echo "FATAL: unknown GOOSE_MODE=${GOOSE_MODE:-<unset>}" >&2
        exit 64
        ;;
esac
