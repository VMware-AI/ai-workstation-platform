"""Immutable telemetry payloads.

Frozen dataclasses so adapter code cannot accidentally mutate an event
after it has been queued for ingest. Field set matches what C1
``/ingest/session`` expects; keeping the shape minimal makes it easy
to bolt new agent backends on top without growing the schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ToolUseEvent:
    """A single tool invocation observed inside an agent session.

    Adapters MUST populate ``agent`` so the ingest side can tell
    claude-code / goose / qcoder apart in dashboards.
    """

    session_id: str
    agent: str
    tool_name: str
    started_at: datetime
    duration_ms: int
    success: bool
    error: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """Lifecycle marker for an agent session.

    ``state`` is ``"started"`` | ``"stopped"`` — keep it stringly typed
    so a new state can be added at the ingest side without bumping the
    shim. ``tokens_in`` / ``tokens_out`` arrive at the stop event;
    leave them at 0 for the start event.
    """

    session_id: str
    agent: str
    state: str
    timestamp: datetime = field(default_factory=_utcnow)
    tokens_in: int = 0
    tokens_out: int = 0
    metadata: Mapping[str, str] = field(default_factory=dict)
