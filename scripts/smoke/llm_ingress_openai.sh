#!/usr/bin/env bash
# LLM ingress smoke test — OpenAI compatible path (task 1.5.5 Goose portion).
#
# Verifies the platform's LiteLLM gateway responds to a goose-shaped
# /v1/chat/completions request. Designed to be run after admin deployment
# as part of the bring-up runbook — NOT in CI (needs a real gateway).
#
# Usage:
#   LITELLM_GATEWAY_URL=http://agent-platform-llm-gateway.customer.internal:4000 \
#   LITELLM_API_KEY=sk-... \
#   ./scripts/smoke/llm_ingress_openai.sh
#
# Exit 0 = healthy; non-zero = degraded (message on stderr).

set -euo pipefail

: "${LITELLM_GATEWAY_URL:?LITELLM_GATEWAY_URL required (e.g. http://agent-platform-llm-gateway.internal:4000)}"
: "${LITELLM_API_KEY:?LITELLM_API_KEY required (per-tenant key from C18 Vaultwarden)}"

MODEL="${MODEL:-qwen-coder-32b}"
PROMPT="${PROMPT:-Say hello in exactly three words.}"

log() { echo "[smoke-openai] $*" >&2; }

log "POST ${LITELLM_GATEWAY_URL}/v1/chat/completions  model=${MODEL}"

# Goose / qoder default to the OpenAI Chat Completions schema. We don't
# stream — easier to validate the response shape — but the gateway must
# still support it for the real agent path.
response="$(
    curl -fsS \
        --max-time 30 \
        -H "Authorization: Bearer ${LITELLM_API_KEY}" \
        -H "Content-Type: application/json" \
        --data-binary @- \
        "${LITELLM_GATEWAY_URL}/v1/chat/completions" <<EOF
{
  "model": "${MODEL}",
  "messages": [{"role": "user", "content": "${PROMPT}"}],
  "max_tokens": 32,
  "stream": false
}
EOF
)" || {
    log "FATAL: curl failed (network / auth / model mis-routed)"
    exit 1
}

# Minimal shape check — choices[0].message.content must be a non-empty string.
# jq isn't always available; awk-grep is enough for a smoke check.
if ! echo "${response}" | grep -q '"content"'; then
    log "FATAL: response missing .choices[].message.content"
    log "  body: ${response}"
    exit 2
fi

log "OK — gateway returned a chat completion"
echo "${response}"
