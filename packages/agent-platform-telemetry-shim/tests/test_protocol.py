"""Protocol-shape tests — Task 1.7.1 acceptance."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_platform_telemetry_shim import SessionEvent, TelemetryShim, ToolUseEvent


class _GoodAdapter:
    """Minimal in-memory adapter used to assert the Protocol contract."""

    agent = "test"

    def __init__(self) -> None:
        self.tool_calls: list[ToolUseEvent] = []
        self.sessions: list[SessionEvent] = []

    def on_tool_use(self, event: ToolUseEvent) -> None:
        self.tool_calls.append(event)

    def on_session(self, event: SessionEvent) -> None:
        self.sessions.append(event)


def test_protocol_runtime_checkable_accepts_compliant_adapter() -> None:
    adapter = _GoodAdapter()
    assert isinstance(adapter, TelemetryShim)


def test_protocol_runtime_checkable_rejects_missing_method() -> None:
    class _Bad:
        agent = "broken"

        def on_tool_use(self, event: ToolUseEvent) -> None:
            pass

    # on_session missing → fails the Protocol contract.
    assert not isinstance(_Bad(), TelemetryShim)


def test_tool_use_event_is_frozen() -> None:
    event = ToolUseEvent(
        session_id="s",
        agent="claude-code",
        tool_name="bash",
        started_at=datetime.now(UTC),
        duration_ms=12,
        success=True,
    )
    # Adapter code must not mutate events after queuing.
    try:
        event.success = False  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("ToolUseEvent must be frozen")


def test_session_event_defaults() -> None:
    event = SessionEvent(session_id="s", agent="goose", state="started")
    assert event.tokens_in == 0
    assert event.tokens_out == 0
    assert event.timestamp.tzinfo is not None
