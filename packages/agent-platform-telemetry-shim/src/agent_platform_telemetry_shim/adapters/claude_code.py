"""Claude Code PostToolUse hook adapter (Task 1.7.2).

Claude Code invokes a configured hook command on every tool use,
piping a JSON envelope on stdin. The envelope looks like::

    {
      "session_id": "<uuid>",
      "transcript_path": "/.claude/projects/.../transcript.jsonl",
      "hook_event_name": "PostToolUse",
      "tool_name": "Bash",
      "tool_input": {...},
      "tool_response": {
        "interrupted": false,
        "isError": false,
        "...": "..."
      }
    }

The adapter:

  1. Reads stdin and converts the envelope into a :class:`ToolUseEvent`.
  2. Hands the event to a :class:`TelemetryShim`-compliant sink
     (typically :class:`IngestClient.push_tool_use` under the hood).
  3. Exits 0 quickly — Claude Code blocks on the hook, so any
     latency here pauses the user-visible loop.

The script is invoked from a hook config like::

    [post_tool_use]
    command = "agent-platform-tele-claude-code"

with the agent's bootstrap bearer + control-plane URL supplied via
the standard ``AGENT_PLATFORM_TELEMETRY_*`` env vars.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..events import SessionEvent, ToolUseEvent
from ..ingest_client import IngestClient

logger = logging.getLogger(__name__)

AGENT_ID = "claude-code"


class _ClockProtocol:
    """Tiny protocol so tests can inject a fake clock without monkey-patching."""

    def __call__(self) -> float: ...  # pragma: no cover


def parse_post_tool_use_payload(
    payload: Mapping[str, Any],
    *,
    started_at: datetime | None = None,
    duration_ms: int | None = None,
) -> ToolUseEvent:
    """Turn a Claude Code PostToolUse envelope into a :class:`ToolUseEvent`.

    Claude Code does not include start time or duration in the
    envelope; the hook itself must measure them (the adapter's CLI
    entry tracks wall-clock between hook start and now, but tests
    can pass explicit values for determinism).

    Raises ``ValueError`` on a malformed envelope so callers can fail
    loudly rather than push half-populated events.
    """
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("payload missing string 'session_id'")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("payload missing string 'tool_name'")

    response = payload.get("tool_response")
    is_error = False
    error_msg: str | None = None
    if isinstance(response, Mapping):
        # Claude Code marks tool failures via either an explicit
        # `isError` flag, an `interrupted` flag, or a non-empty
        # top-level "error" string. Treat any of these as a failure
        # for telemetry purposes.
        is_error = bool(response.get("isError") or response.get("interrupted"))
        err = response.get("error")
        if isinstance(err, str) and err:
            is_error = True
            # Cap like goose/qcoder ([:256]) — telemetry rows must not
            # carry arbitrary stack traces.
            error_msg = err[:256]

    return ToolUseEvent(
        session_id=session_id,
        agent=AGENT_ID,
        tool_name=tool_name,
        started_at=started_at or datetime.now(UTC),
        duration_ms=duration_ms if duration_ms is not None else 0,
        success=not is_error,
        error=error_msg,
    )


@dataclass(slots=True)
class ClaudeCodeAdapter:
    """In-process TelemetryShim that forwards events to an :class:`IngestClient`.

    The dataclass form makes the adapter trivial to construct from
    test fixtures and from the CLI entry point. ``agent`` is a class
    constant — Claude Code is the only thing this adapter handles.
    """

    client: IngestClient

    @property
    def agent(self) -> str:
        return AGENT_ID

    def on_tool_use(self, event: ToolUseEvent) -> None:
        # Sync facade over an async client: the Claude Code hook runs
        # in a fresh process per invocation, so a one-off event loop
        # here is acceptable and avoids requiring callers to live in
        # an async context.
        asyncio.run(self._push_and_close(self.client.push_tool_use, event))

    def on_session(self, event: SessionEvent) -> None:
        asyncio.run(self._push_and_close(self.client.push_session, event))

    async def _push_and_close(self, push: Any, event: Any) -> None:
        # Single-loop push + close, same pattern as goose.py — see
        # [[feedback-async-resource-lifecycle]]. The IngestClient lazily
        # caches an httpx.AsyncClient; leaving it open across two
        # asyncio.run() calls binds its pool to a closed loop and the
        # second call dies with "Event loop is closed".
        try:
            await push(event)
        finally:
            await self.client.aclose()


def _env_or_die(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def main() -> int:
    """CLI entry point — bound to ``agent-platform-tele-claude-code`` in pyproject.

    Reads the PostToolUse envelope from stdin, builds a ToolUseEvent,
    and pushes it. Exits 0 on success or any swallowed-by-design
    transport failure (telemetry is best effort and must not break
    the agent loop); exits non-zero only when the envelope itself is
    so malformed that we cannot construct an event.
    """
    started = time.monotonic()
    started_at = datetime.now(UTC)

    raw = sys.stdin.read()
    if not raw.strip():
        # Empty stdin — Claude Code sometimes invokes hooks with no
        # body during config validation. Treat as no-op success.
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"invalid hook JSON: {exc}", file=sys.stderr)
        return 1

    base_url = _env_or_die("AGENT_PLATFORM_TELEMETRY_BASE_URL")
    token = _env_or_die("AGENT_PLATFORM_TELEMETRY_TOKEN")

    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        event = parse_post_tool_use_payload(payload, started_at=started_at, duration_ms=duration_ms)
    except ValueError as exc:
        print(f"malformed payload: {exc}", file=sys.stderr)
        return 1

    # The adapter's on_tool_use keeps push + close on one event loop —
    # reusing an httpx.AsyncClient across two separate asyncio.run calls
    # would leave its connection pool bound to a closed loop.
    adapter = ClaudeCodeAdapter(client=IngestClient(base_url=base_url, token=token))
    adapter.on_tool_use(event)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
