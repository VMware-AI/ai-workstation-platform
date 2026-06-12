#!/usr/bin/env bash
# agent-platform-llm-gateway 健康检查
# 退出码: 0=全绿 / 1=至少一个失败 / 2=前置缺
set -euo pipefail

BASE="${AGENT_PLATFORM_LLM_GATEWAY_URL:-http://localhost:4000}"
KEY="${AGENT_PLATFORM_LLM_GATEWAY_MASTER_KEY:-sk-CHANGE-ME-master-key}"
MODEL="${AGENT_PLATFORM_LLM_HEALTH_MODEL:-qwen-coder-32b}"
OPENAI_SENTINEL="${AGENT_PLATFORM_LLM_OPENAI_SENTINEL:-agent-platform-openai-ok}"
ANTHROPIC_SENTINEL="${AGENT_PLATFORM_LLM_ANTHROPIC_SENTINEL:-agent-platform-anthropic-ok}"

command -v curl >/dev/null 2>&1 || { echo "curl required"; exit 2; }
command -v jq   >/dev/null 2>&1 || { echo "jq required";   exit 2; }

fail=0
check() {
  local name="$1"; shift
  if "$@"; then
    echo "  PASS $name"
  else
    echo "  FAIL $name" >&2
    fail=$((fail+1))
  fi
}

echo "=== agent-platform-llm-gateway healthcheck ($BASE) ==="

# 1) /health
check "/health" bash -c "curl -fsS '$BASE/health/liveliness' >/dev/null || curl -fsS '$BASE/health' >/dev/null"

# 2) /v1/models 含目标模型
check "/v1/models contains $MODEL" bash -c "
  curl -fsS '$BASE/v1/models' -H 'Authorization: Bearer $KEY' \
    | jq -e '.data[] | select(.id == \"$MODEL\")' >/dev/null
"

# 3) OpenAI 协议 hello-world
check "OpenAI /v1/chat/completions" bash -c "
  curl -fsS '$BASE/v1/chat/completions' \
    -H 'Authorization: Bearer $KEY' -H 'Content-Type: application/json' \
    -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: $OPENAI_SENTINEL\"}],\"max_tokens\":20}' \
    | jq -er '.choices[0].message.content' | grep -F '$OPENAI_SENTINEL' >/dev/null
"

# 4) Anthropic 协议 hello-world (给 Claude Code)
check "Anthropic /anthropic/v1/messages" bash -c "
  curl -fsS '$BASE/anthropic/v1/messages' \
    -H 'x-api-key: $KEY' -H 'anthropic-version: 2023-06-01' -H 'Content-Type: application/json' \
    -d '{\"model\":\"$MODEL\",\"max_tokens\":20,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: $ANTHROPIC_SENTINEL\"}]}' \
    | jq -er '.content[0].text' | grep -F '$ANTHROPIC_SENTINEL' >/dev/null
"

echo
if [ "$fail" -eq 0 ]; then
  echo "all good ✓"
  exit 0
else
  echo "$fail check(s) failed" >&2
  exit 1
fi
