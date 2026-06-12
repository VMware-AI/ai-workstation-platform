"""HTTP push tests — Task 1.7.5 acceptance.

Uses respx to mock the C1 ``/ingest/...`` endpoints; that's cleaner
than a live aiohttp server and lets us assert on the wire payload.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest
import respx
from agent_platform_telemetry_shim import (
    CircuitBreaker,
    IngestClient,
    SessionEvent,
    ToolUseEvent,
)


def _tool_event() -> ToolUseEvent:
    return ToolUseEvent(
        session_id="s1",
        agent="goose",
        tool_name="bash",
        started_at=datetime.now(UTC),
        duration_ms=10,
        success=False,
    )


@pytest.mark.asyncio
async def test_push_session_posts_isoformat_payload() -> None:
    async with respx.mock(base_url="https://c1.example") as router:
        route = router.post("/ingest/session").mock(return_value=httpx.Response(204))
        client = IngestClient(base_url="https://c1.example", token="t")
        event = SessionEvent(
            session_id="s1",
            agent="claude-code",
            state="started",
            timestamp=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
        )
        await client.push_session(event)
        await client.aclose()

        assert route.called
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer t"
        # Timestamp must be JSON-safe ISO format (httpx serialises without spaces).
        assert b'"timestamp":"2026-05-23T12:00:00+00:00"' in sent.content


@pytest.mark.asyncio
async def test_push_tool_use_swallows_5xx_under_breaker() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=60)
    async with respx.mock(base_url="https://c1.example") as router:
        router.post("/ingest/tool-use").mock(return_value=httpx.Response(500))
        client = IngestClient(base_url="https://c1.example", token="t", breaker=breaker)
        event = ToolUseEvent(
            session_id="s1",
            agent="goose",
            tool_name="bash",
            started_at=datetime.now(UTC),
            duration_ms=10,
            success=False,
        )
        # Two 5xx pushes — neither should raise upstream.
        await client.push_tool_use(event)
        await client.push_tool_use(event)
        # Breaker should now be open; a third push must short-circuit.
        await client.push_tool_use(event)
        await client.aclose()

    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_open_breaker_drops_warn_on_first_and_every_100th(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silent breaker-open drops leave zero trace at default log level —
    the 1st and every 100th drop must be a WARNING with the running count."""
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=3600)
    async with respx.mock(base_url="https://c1.example") as router:
        router.post("/ingest/tool-use").mock(return_value=httpx.Response(500))
        client = IngestClient(base_url="https://c1.example", token="t", breaker=breaker)
        await client.push_tool_use(_tool_event())  # 500 → breaker opens
        assert breaker.state == "open"

        caplog.set_level(logging.WARNING, logger="agent_platform_telemetry_shim")
        for _ in range(101):
            await client.push_tool_use(_tool_event())  # all dropped
        await client.aclose()

    drop_warnings = [
        r for r in caplog.records if r.levelno >= logging.WARNING and "dropp" in r.getMessage()
    ]
    assert len(drop_warnings) == 2  # drop #1 and drop #100, not 101 lines
    assert "1" in drop_warnings[0].getMessage()
    assert "100" in drop_warnings[1].getMessage()


@pytest.mark.asyncio
async def test_drop_counter_resets_after_breaker_recovers() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=3600)
    async with respx.mock(base_url="https://c1.example") as router:
        route = router.post("/ingest/tool-use").mock(return_value=httpx.Response(500))
        client = IngestClient(base_url="https://c1.example", token="t", breaker=breaker)
        await client.push_tool_use(_tool_event())  # opens breaker
        await client.push_tool_use(_tool_event())  # dropped
        assert client._dropped == 1

        # Simulate cool-down elapsing + a successful probe.
        breaker._record_success()
        route.mock(return_value=httpx.Response(204))
        await client.push_tool_use(_tool_event())
        await client.aclose()

    assert client._dropped == 0


@pytest.mark.asyncio
async def test_push_session_swallows_transport_error() -> None:
    async with respx.mock(base_url="https://c1.example") as router:
        router.post("/ingest/session").mock(side_effect=httpx.ConnectError("down"))
        client = IngestClient(base_url="https://c1.example", token="t")
        # Must not raise — telemetry is best effort.
        await client.push_session(SessionEvent(session_id="s", agent="qcoder", state="stopped"))
        await client.aclose()


def test_ingest_client_repr_never_leaks_token() -> None:
    client = IngestClient(base_url="https://c1.example", token="bearer-s3cret")
    assert "bearer-s3cret" not in repr(client)
    assert "bearer-s3cret" not in str(client)
