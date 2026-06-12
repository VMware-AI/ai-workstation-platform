# DEPRECATED — agent-platform-portal (C12)

⚠️ **This package is deprecated as of 2026-06-01** — see
[doc 31](../../docs/architecture/31-2026-06-01-user-interaction-model-and-portal-removal.md).

## Why

The standalone user portal is a redundant layer. Under the new interaction
model the **user surface is the agent's own web UI** inside the assigned VM:

> admin assigns a VM (with agents, via C2 console) → user one-click Ollama
> model download + agent start (in-VM) → uses the agent's own web UI.

## Status

- **Removed from the dev/build/install chain** (PR-Rm-1): `make dev-up` no
  longer starts it; the runbook user-install path no longer includes it.
- **Package code retained** for now — do not build new features on it.
- **Full deletion** is planned for GA (PR-Rm-2), a few versions out.

## If you need a user-facing web surface

Use C2 `agent-platform-console` (the single retained frontend) or the agent's
own web UI. Do not revive this portal.
