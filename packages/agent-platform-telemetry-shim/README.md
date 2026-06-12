# agent-platform-telemetry-shim (C7)

Agent-neutral telemetry plumbing for the Agent Platform workstation.

The shim sits **inside** the VM. Three agents (claude-code, goose, qcoder)
each have an adapter that turns their native event stream into our common
`SessionEvent` + `ToolUseEvent` types, and the shim's `IngestClient` pushes
those to the C1 control plane over HTTPS. A 3-state circuit breaker keeps a
hung ingest from blocking the agent loop.

> **Status (2026-06-01)** — Trinity adapters + circuit breaker shipped via
> PR #119. Remaining work is on the C1 side: the `/ingest/session` and
> `/ingest/tool-use` endpoints currently accept-and-drop because the
> control-plane DB schema for telemetry is still being designed (tracked
> in C1 LLD; M2 work). The shim API is **stable for adapter authors**.

## Module layout

```
src/agent_platform_telemetry_shim/
├── protocol.py          TelemetryShim Protocol — Task 1.7.1
├── events.py            ToolUseEvent / SessionEvent (frozen dataclasses)
├── ingest_client.py     HTTP push to C1 — Task 1.7.5
├── circuit_breaker.py   3-state breaker — Task 1.7.6
└── adapters/
    ├── claude_code.py   PostToolUse hook → events
    ├── goose.py         MCP middleware observer
    └── qcoder.py        stdout parser
```

## When to use which module

| You are building... | Use |
|---|---|
| A new agent adapter | `protocol.TelemetryShim` + `events.{SessionEvent,ToolUseEvent}` |
| Sending events to C1 from inside the VM | `IngestClient(base_url, token)` |
| Reasoning about failure modes | `CircuitBreaker` (3-state: closed / open / half_open) |
| Wiring a CLI for a new adapter | follow `adapters/claude_code.py` / `adapters/qcoder.py` (`main()` entry points) |

## Quick example

```python
from datetime import datetime, timezone

from agent_platform_telemetry_shim import IngestClient, SessionEvent

client = IngestClient(base_url="https://c1.agent-platform.internal", token="<agent-bearer>")
try:
    await client.push_session(
        SessionEvent(
            session_id="run-abc",
            agent="claude-code",
            state="stopped",
            timestamp=datetime.now(timezone.utc),
            tokens_in=42_000,
            tokens_out=8_900,
        )
    )
finally:
    await client.aclose()
```

## Bearer token

The C1 control plane issues a **per-VM Bearer token** when the deployment is
provisioned. The token lives at `/etc/agent-platform/telemetry.token` (mode
`0600`, owned by the agent user). Each adapter CLI reads it via `--token-file`
or the `AGENT_PLATFORM_TELEMETRY_TOKEN` env var.

Tokens are not refreshed at runtime; rotating one requires re-provisioning the
VM via the deployment flow. This is intentional — keeping the shim
stateless-on-rotation lets the adapter code stay trivial.

## Failure model

`IngestClient.push_*` **never raises** into the agent runtime. Failures are
classified and degraded:

| Failure | Treatment |
|---|---|
| HTTP 5xx | Log at WARN; breaker increments failure count |
| Transport error (DNS/TCP/TLS) | Log at WARN; breaker increments |
| Breaker open | Drop event; log at DEBUG (already known unhealthy) |
| HTTP 4xx | Log at ERROR; breaker untouched (client bug, not transport) |

After 3 failures within the window, the breaker opens for 30s. On the next
push it transitions to half-open, lets exactly one request through; if that
one succeeds the breaker closes, otherwise back to open for another 30s.

The half-open probe race condition (two callers both thinking they're the
probe) is handled by an explicit state transition under lock — see
`circuit_breaker.py` and [[feedback_circuit_breaker_probe_race]] in the
project memory.

## Adapter authoring contract

A new agent adapter MUST:

1. Implement `protocol.TelemetryShim` (three members: `name`,
   `emit_tool_use`, `emit_session`).
2. Convert native events into **frozen** `ToolUseEvent` / `SessionEvent`
   instances (no mutation after creation — adapters cannot retroactively
   change a queued event).
3. Push via `IngestClient`, not by calling C1 directly. Bypassing the client
   means losing the breaker and the bearer-token plumbing.
4. Ship a thin CLI entry point in `pyproject.toml`
   (`agent-platform-tele-<adapter>`) — keeps systemd unit files simple.

## Local dev

```bash
# Run only the C7 tests (no full workspace sync needed).
uv sync --package agent-platform-telemetry-shim --extra dev
uv run --package agent-platform-telemetry-shim pytest tests/ -v

# Bandit static scan — must report 0 Medium+ issues.
uvx bandit -r src/

# Subtask coverage (Task 1.7) — all six shipped via PR #119:
#   1.7.1 TelemetryShim Protocol      runtime_checkable, 3 members
#   1.7.2 claude-code adapter         PostToolUse hook → events
#   1.7.3 goose adapter               MCP middleware observer
#   1.7.4 qcoder adapter              stdout parser
#   1.7.5 IngestClient.push_session   /ingest/session pump
#   1.7.6 CircuitBreaker              3-state, 30s reset window
```

## Design notes

- **Protocol over ABC** — adapters live in separate runtimes (claude-code is
  JS-side, goose has its own host process). Duck typing keeps each adapter
  free of an import dependency on this package's class hierarchy.
- **Frozen events** — adapters cannot mutate after queuing; ingest sees a
  stable snapshot.
- **Best-effort push** — see §Failure model. The shim is **never** allowed
  to break the agent loop. If telemetry is degraded the agent still runs;
  the operator notices via C1-side aggregate gaps, not via the agent
  crashing.

## Related

- `c7-telemetry-shim.md` LLD in the architecture docs
- C1 `/ingest/session` + `/ingest/tool-use` endpoints (currently accept-and-drop)
- PR #119 — trinity merge
- doc 30 PR-Buf-3 — this README upgrade
