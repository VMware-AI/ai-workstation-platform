"""Goose MCP middleware adapter tests — Task 1.7.3 acceptance."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from agent_platform_telemetry_shim import IngestClient
from agent_platform_telemetry_shim.adapters import GooseAdapter, parse_mcp_tool_result


def test_parse_marks_success_when_iserror_absent() -> None:
    event = parse_mcp_tool_result(
        session_id="goose-sess-1",
        tool_name="shell.run",
        result={"content": [{"type": "text", "text": "ok"}]},
        started_at=datetime(2026, 5, 23, tzinfo=UTC),
        duration_ms=120,
    )
    assert event.agent == "goose"
    assert event.success is True
    assert event.error is None
    assert event.duration_ms == 120


def test_parse_captures_iserror_with_text_payload() -> None:
    event = parse_mcp_tool_result(
        session_id="g",
        tool_name="db.query",
        result={
            "isError": True,
            "content": [{"type": "text", "text": "syntax error near WHERE"}],
        },
        started_at=datetime.now(UTC),
        duration_ms=5,
    )
    assert event.success is False
    assert event.error == "syntax error near WHERE"


def test_parse_handles_iserror_without_text_payload() -> None:
    event = parse_mcp_tool_result(
        session_id="g",
        tool_name="x",
        result={"isError": True},
        started_at=datetime.now(UTC),
        duration_ms=0,
    )
    assert event.success is False
    assert event.error is None


def test_parse_truncates_long_error_messages() -> None:
    long_msg = "x" * 1000
    event = parse_mcp_tool_result(
        session_id="g",
        tool_name="t",
        result={"isError": True, "content": [{"text": long_msg}]},
        started_at=datetime.now(UTC),
        duration_ms=0,
    )
    assert event.error is not None
    assert len(event.error) == 256


def test_parse_rejects_empty_session_id() -> None:
    with pytest.raises(ValueError, match="session_id"):
        parse_mcp_tool_result(
            session_id="",
            tool_name="t",
            result={},
            started_at=datetime.now(UTC),
            duration_ms=0,
        )


def test_parse_rejects_empty_tool_name() -> None:
    with pytest.raises(ValueError, match="tool_name"):
        parse_mcp_tool_result(
            session_id="g",
            tool_name="",
            result={},
            started_at=datetime.now(UTC),
            duration_ms=0,
        )


@pytest.mark.asyncio
async def test_adapter_record_tool_use_pushes_through_client() -> None:
    async with respx.mock(base_url="https://c1.example") as router:
        route = router.post("/ingest/tool-use").mock(return_value=httpx.Response(204))
        client = IngestClient(base_url="https://c1.example", token="t")
        adapter = GooseAdapter(client=client)

        import anyio

        await anyio.to_thread.run_sync(
            lambda: adapter.record_tool_use(
                session_id="sess-x",
                tool_name="shell.run",
                result={"isError": False},
                duration_ms=8,
            )
        )

        assert route.called
