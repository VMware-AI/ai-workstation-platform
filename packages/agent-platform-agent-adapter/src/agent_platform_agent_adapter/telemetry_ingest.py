"""C20 ↔ C7 bridge — push runner telemetry to C1 /ingest/tool-use.

Installs the C7 :class:`IngestClient` behind C20's ``TelemetryShim``
Protocol so the agent runner's existing ``send(event)`` calls fan out
to the control plane without the runner caring about HTTP, retries,
or pool lifecycle.

Optional dependency: ``agent-platform-agent-adapter[ingest]`` brings in
``agent-platform-telemetry-shim`` (and httpx). The import below fails loud
if that extra is missing, so a caller that wires this shim without
the extra installed gets an immediate ImportError rather than a
silently dropped event later.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from .telemetry import TelemetryEvent

try:
    from agent_platform_telemetry_shim import IngestClient, ToolUseEvent
except ImportError as exc:  # pragma: no cover — surfaced at install time
    raise ImportError(
        "telemetry_ingest requires the [ingest] extra: "
        "pip install 'agent-platform-agent-adapter[ingest]'"
    ) from exc

logger = logging.getLogger(__name__)

# C20's TelemetryEvent.agent values aren't constrained by an enum;
# normalise common aliases so C1 dashboards can group consistently.
_AGENT_ALIASES: dict[str, str] = {
    "cc": "claude-code",
    "claude": "claude-code",
    "claude_code": "claude-code",
    "qwen-code": "qcoder",
    "qwen_code": "qcoder",
}


def _normalise_agent(agent: str) -> str:
    return _AGENT_ALIASES.get(agent.lower(), agent.lower())


def _event_to_tool_use(event: TelemetryEvent, session_id: str) -> ToolUseEvent:
    """Map a C20 :class:`TelemetryEvent` to a C7 :class:`ToolUseEvent`.

    Field mapping:

    ===========  ===========
    C20          C7
    ===========  ===========
    agent        agent (aliased)
    tool         tool_name (defaults to "unknown" if the runner had
                 no per-tool context — e.g. a top-level model call)
    duration_s   duration_ms (× 1000, rounded)
    success      success
    error        error (truncated at 256 chars)
    ===========  ===========

    ``session_id`` is owned by the shim, not the per-event payload,
    so the runner can reuse one shim across an agent session.
    """
    return ToolUseEvent(
        session_id=session_id,
        agent=_normalise_agent(event.agent),
        tool_name=event.tool or "unknown",
        started_at=datetime.now(UTC),
        duration_ms=int(event.duration_s * 1000),
        success=event.success,
        error=(event.error[:256] if event.error else None),
    )


@dataclass(slots=True)
class IngestShim:
    """C20 ``TelemetryShim`` implementation backed by C7 ``IngestClient``.

    Best-effort: every transport failure is logged and swallowed —
    the agent runner must never block or crash on telemetry.

    Threading: ``send`` is sync to match C20's Protocol. Each call
    drives a one-shot ``asyncio.run`` over the wrapped async client.
    That's slower than amortising the connection pool over a session,
    but it matches the rest of C20 (single-loop simplicity over
    throughput) and avoids the cross-loop httpx pool hazard noted in
    ``feedback-async-resource-lifecycle``.
    """

    base_url: str
    token: str
    session_id: str
    timeout_s: float = 3.0
    _client: IngestClient | None = None

    def _make_client(self) -> IngestClient:
        return IngestClient(base_url=self.base_url, token=self.token, timeout_s=self.timeout_s)

    def send(self, event: TelemetryEvent) -> None:
        tool_use = _event_to_tool_use(event, self.session_id)
        try:
            asyncio.run(self._send_one(tool_use))
        except Exception as exc:
            logger.warning("ingest push failed: %s", exc)

    async def _send_one(self, event: ToolUseEvent) -> None:
        client = self._make_client()
        try:
            await client.push_tool_use(event)
        finally:
            await client.aclose()

    def flush(self) -> None:
        # IngestClient pools per-call, so there is nothing buffered to
        # flush. Kept for Protocol compliance.
        return None
