"""Three-state circuit breaker (Task 1.7.6).

A telemetry push that hangs must not take the agent runtime down with
it. We treat ingest as best-effort: a configurable number of
consecutive failures opens the breaker; subsequent calls fast-fail with
:class:`CircuitBreakerError` until a cool-down window elapses, then a
single probe call decides whether to close again.

States: CLOSED (normal) → OPEN (fast-fail) → HALF_OPEN (single probe)
→ CLOSED on probe success, → OPEN on probe failure.

Keep the implementation small + dependency-free; ``tenacity`` is
overkill for ~50 lines and the test surface is easier without it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitBreakerError(RuntimeError):
    """Raised when the breaker is open and a call is short-circuited."""


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SUMMARY_MAX = 200


def _summarize(exc: BaseException) -> str:
    """One-line, sanitized failure summary safe to embed in error messages
    (#227): control characters stripped (terminal-escape hygiene, same idea
    as audit.sanitize), truncated so a verbose SDK error can't bloat logs."""
    text = f"{type(exc).__name__}: {exc}"
    text = _CONTROL_CHARS.sub(" ", text)
    if len(text) > _SUMMARY_MAX:
        text = text[:_SUMMARY_MAX] + "…"
    return text


@dataclass(slots=True)
class CircuitBreaker:
    """Trip after ``failure_threshold`` consecutive failures, hold open
    for ``reset_after_s`` seconds, then probe once.

    Thread-safe; an async-only counterpart isn't needed because all
    state mutation is microsecond-scale.
    """

    failure_threshold: int = 3
    reset_after_s: float = 30.0
    # Appears in the CircuitBreakerError message so multi-breaker consumers
    # (e.g. one per vCenter target, H-14 #215) produce teaching errors.
    # Default keeps the historical ingest message for existing consumers.
    name: str = "ingest"

    _state: str = "closed"
    _consecutive_failures: int = 0
    _opened_at: float = 0.0
    _last_failure: str = ""
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # dataclass cannot default a Lock via factory in slots=True
        # without 3.10 boilerplate; assign in post_init instead.
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            if self._state != "closed":
                logger.info("circuit breaker: closing after probe success")
                self._state = "closed"

    def _record_failure(self, exc: BaseException | None = None) -> None:
        with self._lock:
            if exc is not None:
                self._last_failure = _summarize(exc)
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                if self._state != "open":
                    logger.warning(
                        "circuit breaker: opening after %d consecutive failures",
                        self._consecutive_failures,
                    )
                self._state = "open"
                self._opened_at = time.monotonic()

    def peek_open(self) -> bool:
        """True iff the breaker is open AND the cool-down has not elapsed.

        Read-only fast-fail check for callers that hold longer locks (#226):
        never transitions state — the single-probe decision stays inside
        call/call_sync (the "am I the probe" check must transition under
        the breaker lock, see the probe-race lesson).
        """
        with self._lock:
            return self._state == "open" and time.monotonic() - self._opened_at < self.reset_after_s

    def _maybe_half_open(self) -> bool:
        """Return True if the caller is the chosen probe."""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open" and time.monotonic() - self._opened_at >= self.reset_after_s:
                self._state = "half_open"
                logger.info("circuit breaker: half-open, allowing single probe")
                return True
            return False

    def _open_error(self) -> CircuitBreakerError:
        # #227: ops must be able to tell bad-credentials from outage without
        # scrolling back to the pre-trip failures (they may have rolled off).
        # Snapshot under the lock — formally safe beyond CPython's GIL, and
        # the count stays consistent with the summary it explains (#249).
        with self._lock:
            last = self._last_failure
            failures = self._consecutive_failures
        suffix = f" (last failure: {last})" if last else ""
        return CircuitBreakerError(
            f"{self.name} breaker is open after {failures} "
            f"consecutive failures; fast-failing until a probe is allowed "
            f"(~{self.reset_after_s:g}s cool-down){suffix}"
        )

    def open_error(self) -> CircuitBreakerError:
        """Public builder so peek_open() callers raise the same teaching
        error as the internal fast-fail path."""
        return self._open_error()

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` under the breaker. Raises CircuitBreakerError if open."""
        if not self._maybe_half_open():
            raise self._open_error()
        try:
            result = await fn()
        except Exception as exc:
            self._record_failure(exc)
            raise
        self._record_success()
        return result

    def call_sync(self, fn: Callable[[], T]) -> T:
        """Sync counterpart of :meth:`call` for blocking call sites
        (e.g. pyVmomi SmartConnect under asyncio.to_thread, H-14 #215)."""
        if not self._maybe_half_open():
            raise self._open_error()
        try:
            result = fn()
        except Exception as exc:
            self._record_failure(exc)
            raise
        self._record_success()
        return result
