"""Goose MCP middleware adapter (Task 1.7.3).

Block's Goose loads tools through MCP servers. We don't have a stable
public ``MCPMiddleware`` hook to subclass, so the adapter is shaped as
an *observer*: Goose (or a Goose extension) calls ``record_tool_use``
each time an MCP tool finishes. The adapter snapshots the call into a
:class:`ToolUseEvent` and pushes it through the shared
:class:`IngestClient`.

This file ships as a stable Python surface so we can land the
infrastructure now; the actual Goose integration glue (a small Python
or Rust shim that hooks Goose's tool dispatch and calls
``record_tool_use``) is tracked separately — once Goose's middleware
API stabilises we replace the integration without changing this
module.

Provisional fields covered:

* ``session_id`` — Goose session UUID, supplied by the caller
* ``tool_name`` — the MCP server's tool name (e.g. ``shell.run``)
* ``started_at`` / ``duration_ms`` — wall-clock measured by the caller
* ``success`` — derived from whether the MCP response carried an error
* ``error`` — short message text when the response carried an error

The adapter intentionally never inspects the tool's *arguments* —
agent tool inputs are likely to contain user prompts or
file contents, and the C1 telemetry surface only takes
non-sensitive metadata.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..events import SessionEvent, ToolUseEvent
from ..ingest_client import IngestClient

logger = logging.getLogger(__name__)

AGENT_ID = "goose"


def parse_mcp_tool_result(
    *,
    session_id: str,
    tool_name: str,
    result: Mapping[str, Any],
    started_at: datetime,
    duration_ms: int,
) -> ToolUseEvent:
    """Turn an MCP-style tool result envelope into a :class:`ToolUseEvent`.

    MCP responses look like ``{"content": [...], "isError": bool}`` —
    we check ``isError`` and, when present and truthy, sniff the first
    text element of ``content`` for a short error message. Anything
    longer than 256 chars is truncated so telemetry rows don't carry
    arbitrary stack traces.

    Raises ``ValueError`` on missing ``session_id`` / ``tool_name`` so
    the caller cannot silently push half-populated events.
    """
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id must be a non-empty string")
    if not tool_name or not isinstance(tool_name, str):
        raise ValueError("tool_name must be a non-empty string")

    is_error = bool(result.get("isError"))
    error_msg: str | None = None
    if is_error:
        content = result.get("content")
        if isinstance(content, list) and content:
            head = content[0]
            if isinstance(head, Mapping):
                text = head.get("text")
                if isinstance(text, str) and text:
                    error_msg = text[:256]

    return ToolUseEvent(
        session_id=session_id,
        agent=AGENT_ID,
        tool_name=tool_name,
        started_at=started_at,
        duration_ms=duration_ms,
        success=not is_error,
        error=error_msg,
    )


@dataclass(slots=True)
class GooseAdapter:
    """In-process observer Goose (or a glue shim) calls per tool dispatch.

    The dataclass form makes the adapter trivial to construct from
    a glue-shim startup script. ``record_tool_use`` is sync to match
    Goose's middleware-callback shape; internally it drives the async
    :class:`IngestClient` via a one-shot :func:`asyncio.run`.

    Reusing the same :class:`IngestClient` across many
    ``record_tool_use`` calls is unsafe because each call opens its
    own event loop — the httpx pool would be bound to the first
    loop. The adapter therefore expects the caller to flush at
    session end via :meth:`aflush`, called once on stop.
    """

    client: IngestClient

    @property
    def agent(self) -> str:
        return AGENT_ID

    def record_tool_use(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: Mapping[str, Any],
        started_at: datetime | None = None,
        duration_ms: int = 0,
    ) -> None:
        event = parse_mcp_tool_result(
            session_id=session_id,
            tool_name=tool_name,
            result=result,
            started_at=started_at or datetime.now(UTC),
            duration_ms=duration_ms,
        )
        self.on_tool_use(event)

    # ---- TelemetryShim Protocol -----------------------------------------

    def on_tool_use(self, event: ToolUseEvent) -> None:
        asyncio.run(self._push_and_close(self.client.push_tool_use, event))

    def on_session(self, event: SessionEvent) -> None:
        asyncio.run(self._push_and_close(self.client.push_session, event))

    async def _push_and_close(self, push: Any, event: Any) -> None:
        # Single-loop push + close. See [[feedback-async-resource-lifecycle]]
        # — splitting these across two asyncio.run() calls leaves the
        # httpx connection pool bound to a closed loop.
        try:
            await push(event)
        finally:
            await self.client.aclose()
