"""qcoder stdout parser tests — Task 1.7.4 acceptance."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from agent_platform_telemetry_shim import IngestClient, ToolUseEvent
from agent_platform_telemetry_shim.adapters import (
    QcoderAdapter,
    envelope_to_event,
    iter_events,
    parse_qcoder_line,
)
from agent_platform_telemetry_shim.adapters import qcoder as qcoder_mod


def test_parse_line_returns_none_for_non_json_text() -> None:
    assert parse_qcoder_line("hello world") is None
    assert parse_qcoder_line("") is None
    assert parse_qcoder_line("   ") is None


def test_parse_line_returns_none_for_malformed_json() -> None:
    assert parse_qcoder_line("{not valid") is None


def test_parse_line_returns_envelope_for_valid_json() -> None:
    line = json.dumps({"event": "tool_use_end", "session_id": "s", "tool": "shell"})
    env = parse_qcoder_line(line)
    assert env is not None
    assert env["event"] == "tool_use_end"


def test_envelope_to_event_ignores_start_events() -> None:
    env = {"event": "tool_use_start", "session_id": "s", "tool": "shell"}
    assert envelope_to_event(env) is None


def test_envelope_to_event_returns_complete_end_event() -> None:
    env = {
        "event": "tool_use_end",
        "session_id": "sess-7",
        "tool": "shell.run",
        "ts": "2026-05-23T10:00:00+00:00",
        "ms": 42,
        "ok": True,
    }
    event = envelope_to_event(env)
    assert event is not None
    assert event.agent == "qcoder"
    assert event.tool_name == "shell.run"
    assert event.duration_ms == 42
    assert event.success is True
    assert event.started_at == datetime(2026, 5, 23, 10, 0, tzinfo=UTC)


def test_envelope_to_event_marks_error_flagged_event_as_failure() -> None:
    env = {
        "event": "tool_use_end",
        "session_id": "s",
        "tool": "shell",
        "ok": False,
        "error": "permission denied",
    }
    event = envelope_to_event(env)
    assert event is not None
    assert event.success is False
    assert event.error == "permission denied"


def test_envelope_to_event_drops_missing_session_id() -> None:
    env = {"event": "tool_use_end", "tool": "shell"}
    assert envelope_to_event(env) is None


def test_iter_events_skips_assistant_text_lines() -> None:
    lines = iter(
        [
            "Hello, I'll help with that.\n",
            json.dumps({"event": "tool_use_start", "session_id": "s", "tool": "t"}) + "\n",
            json.dumps(
                {"event": "tool_use_end", "session_id": "s", "tool": "t", "ok": True, "ms": 10}
            )
            + "\n",
            "Done.\n",
            "{garbage\n",
        ]
    )
    events = list(iter_events(lines))
    assert len(events) == 1
    assert events[0].tool_name == "t"


@respx.mock(base_url="https://c1.example")
def test_on_tool_use_pushes_immediately_not_buffered(respx_mock: respx.MockRouter) -> None:
    """Each tool-end event is pushed as it arrives (not buffered to EOF), so a
    SIGKILL mid-session keeps already-observed tool uses durable on C1."""
    route = respx_mock.post("/ingest/tool-use").mock(return_value=httpx.Response(204))
    client = IngestClient(base_url="https://c1.example", token="t")
    adapter = QcoderAdapter(client=client)

    for env in [
        {"event": "tool_use_end", "session_id": "s", "tool": "a", "ok": True, "ms": 1},
        {"event": "tool_use_end", "session_id": "s", "tool": "b", "ok": True, "ms": 2},
    ]:
        event = envelope_to_event(env)
        assert event is not None
        adapter.on_tool_use(event)

    # Both pushed without any explicit flush() / EOF — drained eagerly.
    assert route.call_count == 2
    assert not adapter._pending


def test_pending_buffer_is_bounded() -> None:
    """The transient buffer is a bounded deque: even a degenerate caller that
    appends faster than the buffer drains can never grow it without bound."""
    client = IngestClient(base_url="https://c1.example", token="t")
    adapter = QcoderAdapter(client=client)

    assert adapter._pending.maxlen == qcoder_mod.MAX_PENDING
    for i in range(qcoder_mod.MAX_PENDING + 50):
        adapter._pending.append(
            ToolUseEvent(
                session_id="s",
                agent="qcoder",
                tool_name=f"t{i}",
                started_at=datetime.now(UTC),
                duration_ms=1,
                success=True,
                error=None,
            )
        )
    assert len(adapter._pending) <= qcoder_mod.MAX_PENDING


@respx.mock(base_url="https://c1.example")
def test_main_streams_events_per_line(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() pushes each tool-end line as it is read, not all at EOF."""
    route = respx_mock.post("/ingest/tool-use").mock(return_value=httpx.Response(204))
    monkeypatch.setenv("AGENT_PLATFORM_TELEMETRY_BASE_URL", "https://c1.example")
    monkeypatch.setenv("AGENT_PLATFORM_TELEMETRY_TOKEN", "t")
    stdin = io.StringIO(
        json.dumps({"event": "tool_use_end", "session_id": "s", "tool": "a", "ok": True, "ms": 1})
        + "\n"
        + "some assistant text\n"
        + json.dumps({"event": "tool_use_end", "session_id": "s", "tool": "b", "ok": True, "ms": 2})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", stdin)

    assert qcoder_mod.main() == 0
    assert route.call_count == 2


def test_main_exits_2_on_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_TELEMETRY_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_TELEMETRY_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as info:
        qcoder_mod.main()
    assert info.value.code == 2


def test_main_returns_0_with_no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_TELEMETRY_BASE_URL", "https://c1.example")
    monkeypatch.setenv("AGENT_PLATFORM_TELEMETRY_TOKEN", "t")
    # stdin is empty — main should clean-close the lazy pool and return 0
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert qcoder_mod.main() == 0
