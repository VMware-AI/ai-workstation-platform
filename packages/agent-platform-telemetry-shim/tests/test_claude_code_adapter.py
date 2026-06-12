"""Claude Code PostToolUse adapter tests — Task 1.7.2 acceptance."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from agent_platform_telemetry_shim import IngestClient, ToolUseEvent
from agent_platform_telemetry_shim.adapters import (
    ClaudeCodeAdapter,
    parse_post_tool_use_payload,
)
from agent_platform_telemetry_shim.adapters import claude_code as claude_code_mod


def _envelope(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "session_id": "sess-001",
        "transcript_path": "/tmp/transcript.jsonl",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "tool_response": {"interrupted": False, "isError": False},
    }
    base.update(overrides)
    return base


def test_parse_marks_success_when_response_clean() -> None:
    event = parse_post_tool_use_payload(_envelope())
    assert event.agent == "claude-code"
    assert event.session_id == "sess-001"
    assert event.tool_name == "Bash"
    assert event.success is True
    assert event.error is None


def test_parse_flags_iserror_response_as_failure() -> None:
    event = parse_post_tool_use_payload(
        _envelope(tool_response={"isError": True}),
    )
    assert event.success is False
    assert event.error is None  # isError flag alone has no message text


def test_parse_flags_interrupted_response_as_failure() -> None:
    event = parse_post_tool_use_payload(
        _envelope(tool_response={"interrupted": True}),
    )
    assert event.success is False


def test_parse_captures_top_level_error_string() -> None:
    event = parse_post_tool_use_payload(
        _envelope(tool_response={"error": "command not found: foo"}),
    )
    assert event.success is False
    assert event.error == "command not found: foo"


def test_parse_rejects_missing_session_id() -> None:
    with pytest.raises(ValueError, match="session_id"):
        parse_post_tool_use_payload(_envelope(session_id=""))


def test_parse_rejects_missing_tool_name() -> None:
    bad = _envelope()
    del bad["tool_name"]
    with pytest.raises(ValueError, match="tool_name"):
        parse_post_tool_use_payload(bad)


def test_parse_honours_explicit_started_at_and_duration() -> None:
    ts = datetime(2026, 5, 23, 0, 0, tzinfo=UTC)
    event = parse_post_tool_use_payload(_envelope(), started_at=ts, duration_ms=42)
    assert event.started_at == ts
    assert event.duration_ms == 42


@pytest.mark.asyncio
async def test_adapter_pushes_event_through_client() -> None:
    async with respx.mock(base_url="https://c1.example") as router:
        route = router.post("/ingest/tool-use").mock(return_value=httpx.Response(204))
        client = IngestClient(base_url="https://c1.example", token="t")
        adapter = ClaudeCodeAdapter(client=client)

        event = ToolUseEvent(
            session_id="s",
            agent="claude-code",
            tool_name="Read",
            started_at=datetime.now(UTC),
            duration_ms=5,
            success=True,
        )
        # Adapter's on_tool_use is sync but spawns an asyncio.run — we
        # call it inside an existing event loop via to_thread to avoid
        # asyncio.run nesting in the test.
        import anyio

        await anyio.to_thread.run_sync(adapter.on_tool_use, event)
        await client.aclose()

        assert route.called


def test_parse_truncates_long_error_string() -> None:
    """Error text is capped at 256 chars like the goose/qcoder adapters —
    telemetry rows must not carry arbitrary stack traces."""
    event = parse_post_tool_use_payload(
        _envelope(tool_response={"error": "x" * 1000}),
    )
    assert event.success is False
    assert event.error is not None
    assert len(event.error) == 256


def test_adapter_survives_consecutive_sync_calls() -> None:
    """Each on_tool_use/on_session runs its own asyncio.run — the httpx pool
    must be closed within that same loop (push-and-close, like goose.py),
    otherwise the second call reuses a pool bound to a closed loop."""
    with respx.mock(base_url="https://c1.example") as router:
        tool_route = router.post("/ingest/tool-use").mock(return_value=httpx.Response(204))
        session_route = router.post("/ingest/session").mock(return_value=httpx.Response(204))
        client = IngestClient(base_url="https://c1.example", token="t")
        adapter = ClaudeCodeAdapter(client=client)

        event = ToolUseEvent(
            session_id="s",
            agent="claude-code",
            tool_name="Read",
            started_at=datetime.now(UTC),
            duration_ms=5,
            success=True,
        )
        adapter.on_tool_use(event)
        # The pool must be closed after every call so the next asyncio.run
        # starts from a fresh client rather than a closed-loop pool.
        assert client._client is None
        adapter.on_tool_use(event)
        assert client._client is None

        from agent_platform_telemetry_shim import SessionEvent

        adapter.on_session(SessionEvent(session_id="s", agent="claude-code", state="stopped"))
        assert client._client is None

        assert tool_route.call_count == 2
        assert session_route.call_count == 1


def test_main_swallows_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    # No env vars needed — empty stdin returns 0 before the env check.
    assert claude_code_mod.main() == 0


def test_main_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert claude_code_mod.main() == 1


def test_main_rejects_malformed_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_TELEMETRY_BASE_URL", "https://c1.example")
    monkeypatch.setenv("AGENT_PLATFORM_TELEMETRY_TOKEN", "t")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Bash"})))
    assert claude_code_mod.main() == 1


def test_main_exits_2_on_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_TELEMETRY_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_TELEMETRY_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_envelope())))
    with pytest.raises(SystemExit) as info:
        claude_code_mod.main()
    assert info.value.code == 2
