"""Telemetry shim tests — focus on circuit breaker + null safety."""

from __future__ import annotations

import dataclasses
import logging
from unittest.mock import MagicMock

import pytest
from agent_platform_agent_adapter.telemetry import CircuitBreakerShim, NullShim, TelemetryEvent


def _ev() -> TelemetryEvent:
    return TelemetryEvent(
        user_id="alice",
        agent="qcoder",
        model="m",
        tool=None,
        in_tokens=10,
        out_tokens=20,
        duration_s=0.5,
        success=True,
    )


def test_null_shim_send_returns_none():
    n = NullShim()
    assert n.send(_ev()) is None
    assert n.flush() is None


def test_circuit_breaker_open_after_n_failures():
    inner = MagicMock()
    inner.send.side_effect = RuntimeError("boom")
    cb = CircuitBreakerShim(inner, failure_threshold=3)

    for _ in range(3):
        cb.send(_ev())
    assert cb.is_open
    # further sends are no-ops (don't call inner) while the breaker is open
    inner.send.reset_mock()
    cb.send(_ev())
    inner.send.assert_not_called()


def test_circuit_breaker_resets_on_success():
    inner = MagicMock()
    inner.send.side_effect = [RuntimeError("x"), RuntimeError("x"), None]
    cb = CircuitBreakerShim(inner, failure_threshold=3)

    for _ in range(3):
        cb.send(_ev())
    assert not cb.is_open  # one success in middle reset counter


def test_circuit_breaker_never_raises():
    inner = MagicMock()
    inner.send.side_effect = ValueError("anything")
    cb = CircuitBreakerShim(inner, failure_threshold=3)
    # Must not raise no matter how many times
    for _ in range(10):
        cb.send(_ev())


def test_circuit_breaker_manual_reset():
    inner = MagicMock()
    inner.send.side_effect = RuntimeError("x")
    cb = CircuitBreakerShim(inner, failure_threshold=2)
    cb.send(_ev())
    cb.send(_ev())
    assert cb.is_open
    cb.reset()
    assert not cb.is_open


def test_circuit_breaker_recovers_after_cooloff():
    """Three-state breaker: after the cool-down elapses it half-opens and a
    single probe call decides whether to close — it does NOT stay open
    forever (the bug the old two-state shim had)."""
    inner = MagicMock()
    # First 2 calls fail (open the breaker), the probe after cool-off succeeds.
    inner.send.side_effect = [RuntimeError("x"), RuntimeError("x"), None]
    cb = CircuitBreakerShim(inner, failure_threshold=2, reset_after_s=0.0)

    cb.send(_ev())
    cb.send(_ev())
    # The breaker opened (it counted 2 failures), but with a 0s cool-off it is
    # immediately eligible to probe — so the NEXT send is the half-open probe.
    cb.send(_ev())  # probe succeeds → breaker closes
    assert not cb.is_open
    assert inner.send.call_count == 3  # the probe DID reach the inner shim


def test_circuit_breaker_logs_failures_and_open_transition(
    caplog: pytest.LogCaptureFixture,
):
    """Swallowing is fine; swallowing *silently* is not — the closed→open
    transition must leave a WARNING trace so outages are visible in logs."""
    inner = MagicMock()
    inner.send.side_effect = RuntimeError("ingest down")
    cb = CircuitBreakerShim(inner, failure_threshold=2)

    with caplog.at_level(logging.WARNING):
        cb.send(_ev())
        cb.send(_ev())

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("open" in m.lower() for m in messages)  # transition to open
    assert cb.is_open


def test_telemetry_event_immutable():
    ev = _ev()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.user_id = "bob"  # type: ignore[misc]
