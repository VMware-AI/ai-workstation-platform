"""TelemetryShim Protocol (Task 1.7.1).

Adapters (claude-code PostToolUse hook, goose MCP middleware, qcoder
stdout parser) implement this Protocol. The agent-neutral consumer
(C1 ingest, or any other downstream) only depends on the Protocol,
never on a concrete adapter.

Why a Protocol and not an ABC: duck-typing keeps the adapters
independent — a goose middleware doesn't have to subclass a base
class shipped in this package; it just needs the right method shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .events import SessionEvent, ToolUseEvent


@runtime_checkable
class TelemetryShim(Protocol):
    """Contract every agent telemetry adapter satisfies."""

    @property
    def agent(self) -> str:
        """Stable identifier (``"claude-code"`` / ``"goose"`` / ``"qcoder"``).

        Used by ingest to fan out per-agent metrics; never user-facing.
        """
        ...

    def on_tool_use(self, event: ToolUseEvent) -> None:
        """Called once per observed tool invocation.

        Must NOT block longer than a few ms — telemetry is best-effort
        and must not stall the agent runtime. Adapters typically
        delegate to ``IngestClient.push_tool_use`` which is fire-and-
        forget under the circuit breaker.
        """
        ...

    def on_session(self, event: SessionEvent) -> None:
        """Called on session start and stop (Task 1.7.5 entry point).

        Stop events carry the final token counts; intermediate
        progress is reported via :meth:`on_tool_use`.
        """
        ...
