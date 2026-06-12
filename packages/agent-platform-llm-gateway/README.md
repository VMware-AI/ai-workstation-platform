# agent-platform-llm-gateway

LiteLLM Proxy based gateway for M0 agent/LLM evaluation and the future C5
service. It exposes one local endpoint on port `4000` for both OpenAI-compatible
agents and Claude Code's Anthropic-compatible requests.

## Scope

| Protocol | Endpoint | Consumers |
|---|---|---|
| OpenAI | `POST /v1/chat/completions` | qcoder, Goose, eval runner |
| Anthropic | `POST /anthropic/v1/messages` | Claude Code |
| Health | `GET /health` or `/health/liveliness` | systemd, smoke tests |
| Models | `GET /v1/models` | install docs, healthcheck |

The gateway routes named models such as `qwen-coder-32b` to local vLLM OpenAI
servers. Token/spend logs are written through LiteLLM's Postgres callbacks when
`database_url` is configured.

## Subtask coverage (Task 1.5)

| Sub | Status | Notes |
|---|---|---|
| **1.5.1** LiteLLM Proxy config | ✅ | `config/config.yaml.example` + validator in `agent_platform_llm_gateway.config` |
| 1.5.2 Anthropic ingress | ✅ | `anthropic_passthrough_endpoint` in config |
| 1.5.3 Per-user API keys | ⬜ | needs agent-platform-control `/key/generate` (1.5.3) |
| 1.5.4 Token usage → Postgres | ⬜ | LiteLLM-side already wired via `success_callback: [postgres]`; C1 ETL pending |
| 1.5.5 OpenAI ingress | ✅ | LiteLLM default `/v1/chat/completions` |
| **1.5.6** rpm/tpm log warning | ✅ | validator refuses to start unless both are set; LiteLLM logs at WARN on excess |

## Install

```bash
uv tool install agent-platform-llm-gateway
```

This installs the `agent-platform-llm-gateway` console script which is what the systemd unit + start.sh prefer. Without it, start.sh falls back to raw `litellm` (no config validation). Set `AGENT_PLATFORM_LLM_GATEWAY_WORKERS` to override the default two LiteLLM workers.

## Quick Start

Copy and edit the sample config:

```bash
cp packages/agent-platform-llm-gateway/config/config.yaml.example /tmp/agent-platform-litellm.yaml
```

For local M0 evaluation, set these values first:

```yaml
general_settings:
  master_key: sk-eval-master-key
  database_url: sqlite:///tmp/agent-platform-litellm.db
```

Start the proxy:

```bash
AGENT_PLATFORM_LLM_GATEWAY_CONFIG=/tmp/agent-platform-litellm.yaml \
AGENT_PLATFORM_LLM_GATEWAY_PORT=4000 \
packages/agent-platform-llm-gateway/scripts/start.sh
```

Run the bundled smoke test:

```bash
AGENT_PLATFORM_LLM_GATEWAY_URL=http://localhost:4000 \
AGENT_PLATFORM_LLM_GATEWAY_MASTER_KEY=sk-eval-master-key \
AGENT_PLATFORM_LLM_HEALTH_MODEL=qwen-coder-32b \
packages/agent-platform-llm-gateway/scripts/healthcheck.sh
```

The script checks `/health`, `/v1/models`, OpenAI chat, and Anthropic messages.
The final two checks require at least one backend vLLM server from PR #49.

## Manual Acceptance Curls

Issue #5 requires port `4000` plus one OpenAI and one Anthropic request.

OpenAI-compatible request:

```bash
curl -fsS http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-eval-master-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-coder-32b",
    "messages": [{"role": "user", "content": "Reply with exactly: agent-platform-openai-ok"}],
    "max_tokens": 20
  }'
```

Anthropic-compatible request:

```bash
curl -fsS http://localhost:4000/anthropic/v1/messages \
  -H "x-api-key: sk-eval-master-key" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-coder-32b",
    "max_tokens": 20,
    "messages": [{"role": "user", "content": "Reply with exactly: agent-platform-anthropic-ok"}]
  }'
```

Claude Code should point at the same Anthropic-compatible path without `/v1`:

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000/anthropic
export ANTHROPIC_API_KEY=sk-eval-master-key
```

## Verification Record

Copy this table into the PR or issue comment after running against a live
backend:

| Check | Expected | Result |
|---|---|---|
| Gateway port | `localhost:4000` listening | |
| `/v1/models` | contains `qwen-coder-32b` | |
| OpenAI curl | response text includes `agent-platform-openai-ok` | |
| Anthropic curl | response text includes `agent-platform-anthropic-ok` | |
| Claude Code env | `ANTHROPIC_BASE_URL=http://localhost:4000/anthropic` | |

## Production Notes

- The systemd unit expects `/etc/agent-platform-llm-gateway/config.yaml`.
- Use Postgres for token accounting in shared environments.
- Keep `master_key` and generated virtual keys out of git.
- Register explicit model names for production; the Claude alias is only for M0
  compatibility testing.
