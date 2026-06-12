"""Circuit-breaker state machine tests — Task 1.7.6 acceptance."""

from __future__ import annotations

import time

import pytest
from agent_platform_telemetry_shim import CircuitBreaker, CircuitBreakerError


@pytest.mark.asyncio
async def test_closed_initial_state() -> None:
    breaker = CircuitBreaker()
    assert breaker.state == "closed"

    async def ok() -> int:
        return 1

    assert await breaker.call(ok) == 1
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_opens_after_threshold_failures() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=60)

    async def boom() -> None:
        raise RuntimeError("nope")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(boom)
    assert breaker.state == "open"

    async def never_called() -> None:
        raise AssertionError("breaker should have short-circuited")

    with pytest.raises(CircuitBreakerError):
        await breaker.call(never_called)


@pytest.mark.asyncio
async def test_half_open_probe_closes_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=0.05)

    async def boom() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await breaker.call(boom)
    assert breaker.state == "open"

    # Wait past the reset window — the next call becomes the probe.
    time.sleep(0.06)

    async def ok() -> str:
        return "alive"

    assert await breaker.call(ok) == "alive"
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_half_open_allows_only_one_probe() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=0.05)

    async def boom() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await breaker.call(boom)

    time.sleep(0.06)
    assert breaker._maybe_half_open()  # first caller becomes the probe
    assert breaker.state == "half_open"
    assert not breaker._maybe_half_open()  # concurrent callers short-circuit


@pytest.mark.asyncio
async def test_half_open_probe_reopens_on_failure() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=0.05)

    async def boom() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await breaker.call(boom)

    time.sleep(0.06)

    # The probe fails — breaker should snap back to open.
    with pytest.raises(RuntimeError):
        await breaker.call(boom)
    assert breaker.state == "open"


# ----- call_sync (harness H-14, #215 — vCenter SmartConnect is sync) ---------


def test_call_sync_happy_path() -> None:
    breaker = CircuitBreaker()
    assert breaker.call_sync(lambda: 42) == 42
    assert breaker.state == "closed"


def test_call_sync_opens_after_threshold_and_fast_fails() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=60)

    def boom() -> None:
        raise RuntimeError("nope")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call_sync(boom)
    assert breaker.state == "open"

    calls: list[int] = []

    def should_not_run() -> None:
        calls.append(1)

    with pytest.raises(CircuitBreakerError):
        breaker.call_sync(should_not_run)
    assert calls == []  # fast-fail: the wrapped fn never ran


def test_call_sync_probe_closes_after_reset_window() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=0.05)

    with pytest.raises(RuntimeError):
        breaker.call_sync(lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    assert breaker.state == "open"

    time.sleep(0.06)
    assert breaker.call_sync(lambda: "alive") == "alive"
    assert breaker.state == "closed"


def test_breaker_name_appears_in_error() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=60, name="vCenter vc1:443")

    with pytest.raises(RuntimeError):
        breaker.call_sync(lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    with pytest.raises(CircuitBreakerError, match=r"vCenter vc1:443"):
        breaker.call_sync(lambda: 1)


@pytest.mark.asyncio
async def test_async_error_message_keeps_default_name() -> None:
    """Existing consumers (ingest_client) keep the historical message."""
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=60)

    async def boom() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await breaker.call(boom)
    with pytest.raises(CircuitBreakerError, match="ingest breaker is open"):
        await breaker.call(boom)


def test_open_error_carries_sanitized_last_failure_summary() -> None:
    """#227: ops must be able to tell bad-credentials from outage without
    scrolling back to the pre-trip failures."""
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=60, name="vCenter vc1:443")

    class InvalidLogin(RuntimeError):  # noqa: N818 — mirrors pyVmomi vim.fault.InvalidLogin
        pass

    with pytest.raises(InvalidLogin):
        breaker.call_sync(
            lambda: (_ for _ in ()).throw(InvalidLogin("Cannot complete login due to bad creds"))
        )
    with pytest.raises(
        CircuitBreakerError, match=r"last failure: InvalidLogin: Cannot complete login"
    ):
        breaker.call_sync(lambda: 1)


def test_last_failure_summary_is_truncated_and_control_chars_stripped() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=60)
    noisy = "x" * 500 + "\x1b[31mevil\x07\n"

    with pytest.raises(RuntimeError):
        breaker.call_sync(lambda: (_ for _ in ()).throw(RuntimeError(noisy)))
    try:
        breaker.call_sync(lambda: 1)
        raise AssertionError("expected CircuitBreakerError")
    except CircuitBreakerError as e:
        msg = str(e)
        assert "\x1b" not in msg and "\x07" not in msg and "\n" not in msg
        assert len(msg) < 400  # summary capped, not the raw 500+ chars


def test_peek_open_reads_without_consuming_the_probe() -> None:
    """#226: callers use peek_open() to fast-fail BEFORE acquiring the
    per-target connect lock. It must never transition state — the probe
    decision stays inside call/call_sync (see probe-race lesson)."""
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=0.05)

    assert breaker.peek_open() is False  # closed
    with pytest.raises(RuntimeError):
        breaker.call_sync(lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    assert breaker.peek_open() is True  # open, cool-down running
    assert breaker.state == "open"

    time.sleep(0.06)
    # Cool-down elapsed: peek says "go take the real path"…
    assert breaker.peek_open() is False
    # …but it must NOT have flipped state to half_open itself.
    assert breaker.state == "open"
    # The actual probe still works exactly once via call_sync.
    assert breaker.call_sync(lambda: "alive") == "alive"
    assert breaker.state == "closed"
