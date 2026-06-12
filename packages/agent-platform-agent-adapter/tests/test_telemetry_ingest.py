"""Tests for the C20 ↔ C7 bridge.

Mocks the C7 IngestClient HTTP layer with respx so we exercise the
real bridge surface (event mapping, sync facade, failure swallowing).
"""

from __future__ import annotations

import httpx
import respx
from agent_platform_agent_adapter.telemetry import TelemetryEvent
from agent_platform_agent_adapter.telemetry_ingest import (
    IngestShim,
    _event_to_tool_use,
    _normalise_agent,
)


def test_normalise_agent_aliases() -> None:
    assert _normalise_agent("cc") == "claude-code"
    assert _normalise_agent("Claude") == "claude-code"
    assert _normalise_agent("claude_code") == "claude-code"
    assert _normalise_agent("qwen-code") == "qcoder"
    assert _normalise_agent("qwen_code") == "qcoder"


def test_normalise_agent_passthrough_for_canonical_names() -> None:
    assert _normalise_agent("goose") == "goose"
    assert _normalise_agent("qcoder") == "qcoder"


def test_event_to_tool_use_maps_all_fields() -> None:
    event = TelemetryEvent(
        user_id="alice",
        agent="cc",
        model="qwen-coder-32b",
        tool="bash",
        in_tokens=100,
        out_tokens=50,
        duration_s=1.234,
        success=True,
        error=None,
    )
    out = _event_to_tool_use(event, session_id="sess-1")
    assert out.session_id == "sess-1"
    assert out.agent == "claude-code"
    assert out.tool_name == "bash"
    assert out.duration_ms == 1234
    assert out.success is True
    assert out.error is None


def test_event_to_tool_use_defaults_missing_tool() -> None:
    event = TelemetryEvent(
        user_id="alice",
        agent="goose",
        model=None,
        tool=None,  # top-level model call without a per-tool context
        in_tokens=None,
        out_tokens=None,
        duration_s=0.5,
        success=True,
    )
    out = _event_to_tool_use(event, session_id="s")
    assert out.tool_name == "unknown"


def test_event_to_tool_use_truncates_long_error() -> None:
    long_msg = "x" * 1000
    event = TelemetryEvent(
        user_id="alice",
        agent="cc",
        model=None,
        tool="bash",
        in_tokens=None,
        out_tokens=None,
        duration_s=0.1,
        success=False,
        error=long_msg,
    )
    out = _event_to_tool_use(event, session_id="s")
    assert out.error is not None
    assert len(out.error) == 256


def test_shim_send_pushes_to_ingest() -> None:
    with respx.mock(base_url="https://c1.example") as router:
        route = router.post("/ingest/tool-use").mock(return_value=httpx.Response(204))
        shim = IngestShim(base_url="https://c1.example", token="t", session_id="sess-x")
        shim.send(
            TelemetryEvent(
                user_id="alice",
                agent="cc",
                model="m",
                tool="bash",
                in_tokens=10,
                out_tokens=20,
                duration_s=0.5,
                success=True,
            )
        )
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer t"
        assert b'"agent":"claude-code"' in sent.content
        assert b'"session_id":"sess-x"' in sent.content


def test_shim_send_swallows_transport_error() -> None:
    with respx.mock(base_url="https://c1.example") as router:
        router.post("/ingest/tool-use").mock(side_effect=httpx.ConnectError("down"))
        shim = IngestShim(base_url="https://c1.example", token="t", session_id="s")
        # Must NOT raise — telemetry is best effort.
        shim.send(
            TelemetryEvent(
                user_id="alice",
                agent="goose",
                model=None,
                tool="x",
                in_tokens=None,
                out_tokens=None,
                duration_s=0.1,
                success=True,
            )
        )


def test_shim_send_swallows_5xx() -> None:
    with respx.mock(base_url="https://c1.example") as router:
        router.post("/ingest/tool-use").mock(return_value=httpx.Response(500))
        shim = IngestShim(base_url="https://c1.example", token="t", session_id="s")
        # IngestClient logs + swallows internally; shim wraps any
        # bubble-up in its own try/except so the runner never sees it.
        shim.send(
            TelemetryEvent(
                user_id="alice",
                agent="qcoder",
                model=None,
                tool="x",
                in_tokens=None,
                out_tokens=None,
                duration_s=0.0,
                success=True,
            )
        )


def test_shim_flush_is_noop() -> None:
    shim = IngestShim(base_url="https://c1.example", token="t", session_id="s")
    # No exception, no side effect — just exists for Protocol compliance.
    shim.flush()


def test_shim_satisfies_telemetry_shim_protocol() -> None:
    """The bridge should be substitutable for any other C20 TelemetryShim."""
    from agent_platform_agent_adapter.telemetry import CircuitBreakerShim

    shim = IngestShim(base_url="https://c1.example", token="t", session_id="s")
    # Wrap in the project's own circuit breaker — if the Protocol
    # contract is satisfied this should construct without TypeError.
    breaker = CircuitBreakerShim(shim, failure_threshold=3)
    assert breaker.is_open is False
