"""TelemetryShim: best-effort circuit-breaker that swallows failures.

Why a Protocol rather than a hard dep on agent-platform-control:
- adapters live in VMs that may not have control-plane reachability
- telemetry failures must not break agent calls (3-strike circuit breaker)
- M1.7 will provide concrete implementations (PostHookShim, MCPMiddlewareShim,
  StdoutParserShim) that wrap this protocol.

Reference: CLAUDE.md error-recovery layering (轻量重试 → 教学性错误 → circuit breaker).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Protocol

from agent_platform_telemetry_shim import CircuitBreaker, CircuitBreakerError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryEvent:
    """One observation. agent / tool / token / duration are best-effort."""

    user_id: str
    agent: str
    model: str | None
    tool: str | None
    in_tokens: int | None
    out_tokens: int | None
    duration_s: float
    success: bool
    error: str | None = None


class TelemetryShim(Protocol):
    """Sink. Implementations must NOT raise on send() — they should swallow."""

    def send(self, event: TelemetryEvent) -> None: ...
    def flush(self) -> None: ...


# === Built-in implementations ===


class NullShim:
    """No-op. Useful default + tests."""

    def send(self, event: TelemetryEvent) -> None:
        return None

    def flush(self) -> None:
        return None


class CircuitBreakerShim:
    """Wraps another shim behind C7's three-state :class:`CircuitBreaker`.

    After ``failure_threshold`` consecutive failures the breaker opens and
    further sends fast-fail (the inner shim is not called). Unlike the old
    two-state implementation this is NOT permanent: once ``reset_after_s``
    elapses the breaker half-opens and a single probe send decides whether
    to close again — a transient ingest outage self-heals without a process
    restart.

    Best-effort throughout: ``send`` never raises into the agent loop, and
    the closed→open transition is logged at WARNING by the breaker so
    outages stay visible.
    """

    def __init__(
        self,
        inner: TelemetryShim,
        failure_threshold: int = 3,
        *,
        reset_after_s: float = 30.0,
    ):
        self._inner = inner
        self._failure_threshold = failure_threshold
        self._reset_after_s = reset_after_s
        self._breaker = self._new_breaker()

    def _new_breaker(self) -> CircuitBreaker:
        return CircuitBreaker(
            failure_threshold=self._failure_threshold,
            reset_after_s=self._reset_after_s,
            name="telemetry",
        )

    @property
    def is_open(self) -> bool:
        # peek_open() is True only while open AND inside the cool-down window;
        # once the window elapses the breaker is ready to probe (logically
        # half-open) and we report it as no longer open.
        return self._breaker.peek_open()

    def send(self, event: TelemetryEvent) -> None:
        try:
            self._breaker.call_sync(lambda: self._inner.send(event))
        except CircuitBreakerError:
            # Breaker open — fast-fail, drop the event. The breaker already
            # logged the open transition; a per-drop log would be noise.
            return
        except Exception as exc:
            # Inner send raised; the breaker has counted the failure. Swallow
            # so the agent loop is never blocked by telemetry.
            logger.warning("telemetry send failed: %s", exc)

    def flush(self) -> None:
        if self.is_open:
            return
        with contextlib.suppress(Exception):
            self._inner.flush()

    def reset(self) -> None:
        # Drop the breaker for a fresh closed one — simplest way to clear all
        # internal state (the C7 breaker exposes no public reset).
        self._breaker = self._new_breaker()
