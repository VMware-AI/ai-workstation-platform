#!/usr/bin/env bash
# LLM ingress smoke test — Anthropic passthrough path (task 1.5.2).
#
# Verifies the platform's LiteLLM gateway responds to a Claude Code-shaped
# /v1/messages call (the Anthropic passthrough endpoint). Designed to be
# run after admin deployment as part of the bring-up runbook — NOT in CI.
#
# Usage:
#   LITELLM_GATEWAY_URL=http://agent-platform-llm-gateway.customer.internal:4000 \
#   ANTHROPIC_API_KEY=sk-ant-... \
#   ./scripts/smoke/llm_ingress_anthropic.sh
#
# Exit 0 = healthy; non-zero = degraded.

set -euo pipefail

: "${LITELLM_GATEWAY_URL:?LITELLM_GATEWAY_URL required}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY required (per-tenant key from C18)}"

MODEL="${MODEL:-claude-3-5-sonnet-latest}"
PROMPT="${PROMPT:-Say hello in exactly three words.}"

log() { echo "[smoke-anthropic] $*" >&2; }

log "POST ${LITELLM_GATEWAY_URL}/anthropic/v1/messages  model=${MODEL}"

response="$(
    curl -fsS \
        --max-time 30 \
        -H "x-api-key: ${ANTHROPIC_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -H "Content-Type: application/json" \
        --data-binary @- \
        "${LITELLM_GATEWAY_URL}/anthropic/v1/messages" <<EOF
{
  "model": "${MODEL}",
  "max_tokens": 32,
  "messages": [{"role": "user", "content": "${PROMPT}"}]
}
EOF
)" || {
    log "FATAL: curl failed (network / auth / passthrough mis-routed)"
    exit 1
}

if ! echo "${response}" | grep -q '"text"'; then
    log "FATAL: response missing .content[].text"
    log "  body: ${response}"
    exit 2
fi

log "OK — Anthropic passthrough returned a message"
echo "${response}"
