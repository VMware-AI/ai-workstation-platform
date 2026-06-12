# LLM ingress smoke tests

Post-deployment runbook smokes (tasks 1.5.2 + 1.5.5). They are **not** in
CI — they need a live LiteLLM gateway and a real upstream LLM API key.

Run them once after standing up C5 (`agent-platform-llm-gateway`) in a customer
environment, save the output to the on-call runbook, and re-run when the
LLM ingress changes (new model, new upstream, rotated key).

## Files

| Script | Path it hits | Used by |
|---|---|---|
| `llm_ingress_openai.sh` | `POST /v1/chat/completions` | Goose / qoder / xiaoguai (OpenAI-compatible agents) |
| `llm_ingress_anthropic.sh` | `POST /anthropic/v1/messages` | Claude Code passthrough (task 0.3 / 1.5.2) |

## Required env

| Var | Source |
|---|---|
| `LITELLM_GATEWAY_URL` | Customer-side admin (the public URL of C5) |
| `LITELLM_API_KEY` | Per-tenant key from C18 Vaultwarden (`tenant/{id}/llm_gateway_api_key`) |
| `ANTHROPIC_API_KEY` | Anthropic key from C18 (only for the Anthropic script) |

Optional overrides: `MODEL`, `PROMPT`.

## What "passing" looks like

- Exit code 0
- Response printed to stdout contains a non-empty completion / message text

## What "failing" looks like, and how to fix

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl: (7) Failed to connect` | gateway not reachable | check DNS / firewall from caller side; `curl -v ${LITELLM_GATEWAY_URL}/healthz` |
| `401 Unauthorized` | wrong / rotated API key | re-read from C18 Vaultwarden |
| `404 Not Found` on `/v1/chat/completions` | gateway misrouted | check LiteLLM config — model alias must map to a registered upstream |
| `response missing .content[]` | upstream returned an error envelope | check gateway logs for the upstream stderr |
| Timeout (30s) | upstream slow / overloaded | retry; if persistent, swap to a different model alias |

## Closing the M0/M1 tasks

These scripts plus a saved successful run satisfy the acceptance points
on tasks 1.5.2 + 1.5.5 (Goose portion). The `qcoder` portion of 1.5.5
stays frozen per the 2026-05-28 product decision.
