"""qcoder stdout parser adapter (Task 1.7.4).

qcoder (Qwen Code open-source) emits its tool-call lifecycle as
single-line JSON events on stdout. The exact line schema depends on
the qcoder build the agent VM ships; the parser below recognises the
shape we've seen in M0 evaluation:

    {"event": "tool_use_start", "session_id": "...", "tool": "shell", "ts": "<iso>"}
    {"event": "tool_use_end",   "session_id": "...", "tool": "shell",
     "ts": "<iso>", "ok": true, "ms": 123, "error": null}

A non-JSON line or any line whose ``event`` is not in the recognised
set is ignored (qcoder also prints assistant text on stdout — we
must not crash on that).

CLI usage::

    qcoder run --... | agent-platform-tele-qcoder

reads stdin and pushes one event per ``tool_use_end`` line *as it is
read* — telemetry is never buffered to EOF, so a crash / SIGKILL
mid-session keeps every already-observed tool use durable on C1.
``AGENT_PLATFORM_TELEMETRY_BASE_URL`` and ``AGENT_PLATFORM_TELEMETRY_TOKEN`` are
required env vars.

Field assumptions are flagged ``# PROVISIONAL`` so a future build of
qcoder that changes the schema is a one-place fix.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..events import SessionEvent, ToolUseEvent
from ..ingest_client import IngestClient

logger = logging.getLogger(__name__)

AGENT_ID = "qcoder"

# Upper bound on the transient in-memory buffer. qcoder runs as a
# long-lived process; without a cap a pathological producer (or a
# caller that appends without draining) could grow the buffer until the
# agent VM OOMs. A bounded deque drops the oldest events once full —
# losing the stalest telemetry is strictly better than killing the VM.
MAX_PENDING = 1024

# PROVISIONAL — confirm against the qcoder build in C3 agent-platform-image.
TOOL_END_EVENTS = frozenset({"tool_use_end"})
TOOL_START_EVENTS = frozenset({"tool_use_start"})


def _parse_ts(raw: Any) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC)


def parse_qcoder_line(line: str) -> Mapping[str, Any] | None:
    """Parse a single qcoder stdout line. Returns None on non-JSON / unknown event.

    Caller is expected to feed every stdout line — non-event noise
    (assistant text, debug prints) is silently dropped.
    """
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def envelope_to_event(envelope: Mapping[str, Any]) -> ToolUseEvent | None:
    """Convert a parsed JSON envelope into a :class:`ToolUseEvent`.

    Only ``tool_use_end`` envelopes produce a complete event (start
    events have no duration / outcome). Returns None for envelopes
    that aren't tool-end events or are missing required fields.
    """
    event_name = envelope.get("event")
    if event_name not in TOOL_END_EVENTS:
        return None

    session_id = envelope.get("session_id")
    tool_name = envelope.get("tool")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(tool_name, str) or not tool_name:
        return None

    duration_ms = envelope.get("ms", 0)
    if not isinstance(duration_ms, int):
        duration_ms = 0

    ok = envelope.get("ok", True)
    error_raw = envelope.get("error")
    error_msg: str | None = None
    if isinstance(error_raw, str) and error_raw:
        error_msg = error_raw[:256]

    return ToolUseEvent(
        session_id=session_id,
        agent=AGENT_ID,
        tool_name=tool_name,
        started_at=_parse_ts(envelope.get("ts")),
        duration_ms=duration_ms,
        success=bool(ok) and error_msg is None,
        error=error_msg,
    )


def iter_events(lines: Iterator[str]) -> Iterator[ToolUseEvent]:
    """Yield :class:`ToolUseEvent` for every recognised tool-end line."""
    for raw in lines:
        envelope = parse_qcoder_line(raw)
        if envelope is None:
            continue
        event = envelope_to_event(envelope)
        if event is not None:
            yield event


@dataclass(slots=True)
class QcoderAdapter:
    """Streaming adapter that pushes a session's tool-end events eagerly.

    Each :meth:`on_tool_use` / :meth:`on_session` call pushes the event
    immediately rather than buffering until EOF — a crash or SIGKILL
    mid-session therefore loses at most the in-flight event, not the
    whole session.

    qcoder runs as a long-lived process, so we can't keep one
    :class:`IngestClient` pool open across many pushes: each push opens
    its own event loop via :func:`asyncio.run`, and reusing the lazily
    cached ``httpx.AsyncClient`` across two loops binds the pool to a
    closed loop (see [[feedback-async-resource-lifecycle]], and the
    push-and-close pattern PR #349 landed for claude_code/goose). The
    :class:`IngestClient` recreates its pool lazily after each
    ``aclose()``, so push-then-close per event is safe.

    ``_pending`` is a bounded transient buffer (drained on every push);
    its cap guarantees memory can't grow without bound even if the
    drain can't keep up.
    """

    client: IngestClient
    _pending: deque[ToolUseEvent | SessionEvent] = field(
        default_factory=lambda: deque(maxlen=MAX_PENDING)
    )

    @property
    def agent(self) -> str:
        return AGENT_ID

    def on_tool_use(self, event: ToolUseEvent) -> None:
        self._pending.append(event)
        self._drain()

    def on_session(self, event: SessionEvent) -> None:
        # qcoder doesn't currently emit a session-stop sentinel; the
        # CLI driver constructs the SessionEvent at EOF and calls
        # this hook directly.
        self._pending.append(event)
        self._drain()

    def _drain(self) -> None:
        """Push every buffered event on its own short-lived event loop + pool."""
        while self._pending:
            event = self._pending.popleft()
            asyncio.run(self._push_and_close(event))

    async def _push_and_close(self, event: ToolUseEvent | SessionEvent) -> None:
        # Single-loop push + close — see the class docstring. push_* on
        # IngestClient swallows transport / breaker failures (telemetry
        # is best effort), so this never raises into the agent loop.
        try:
            if isinstance(event, SessionEvent):
                await self.client.push_session(event)
            else:
                await self.client.push_tool_use(event)
        finally:
            await self.client.aclose()

    async def flush(self) -> None:
        """Drain any residual buffered events then close the pool.

        With eager per-event pushes ``_pending`` is normally empty here;
        flush exists for Protocol symmetry and to close cleanly at EOF.
        """
        try:
            while self._pending:
                event = self._pending.popleft()
                if isinstance(event, SessionEvent):
                    await self.client.push_session(event)
                else:
                    await self.client.push_tool_use(event)
        finally:
            await self.client.aclose()


def _env_or_die(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error("missing required env var: %s", name)
        sys.exit(2)
    return value


def main() -> int:
    """CLI entry — read qcoder stdout, push each tool-use event, exit 0."""
    base_url = _env_or_die("AGENT_PLATFORM_TELEMETRY_BASE_URL")
    token = _env_or_die("AGENT_PLATFORM_TELEMETRY_TOKEN")

    client = IngestClient(base_url=base_url, token=token)
    adapter = QcoderAdapter(client=client)
    # iter_events streams: each tool-end line is parsed and pushed before
    # the next line is read, so telemetry survives a mid-session crash.
    for event in iter_events(iter(sys.stdin)):
        adapter.on_tool_use(event)

    # Pool is recreated + closed per push; nothing is left open here.
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
