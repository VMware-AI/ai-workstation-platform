"""HTTP push client for C1 ``/ingest/session`` (Task 1.7.5).

Wraps an :class:`httpx.AsyncClient` and a :class:`CircuitBreaker`. The
client is best-effort: callers fire and forget, and a tripped breaker
swallows the call rather than raising up into the agent runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import httpx

from .circuit_breaker import CircuitBreaker, CircuitBreakerError
from .events import SessionEvent, ToolUseEvent

logger = logging.getLogger(__name__)


class IngestError(RuntimeError):
    """Raised when the C1 ingest endpoint returns a non-2xx response.

    Internal: the public push helpers swallow this so the agent
    runtime is never blocked. Tests assert on it directly.
    """


def _event_to_json(event: SessionEvent | ToolUseEvent) -> dict[str, Any]:
    """Marshal a frozen dataclass into JSON-safe primitives."""
    out: dict[str, Any] = {}
    for key, value in asdict(event).items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


@dataclass(slots=True)
class IngestClient:
    """Push :class:`ToolUseEvent` / :class:`SessionEvent` to C1.

    ``base_url`` points at the control-plane (e.g.
    ``https://control.agent-platform.internal``); the trailing ``/ingest/...``
    paths are appended internally.

    ``token`` is the agent's bearer; ``timeout_s`` bounds a single
    push; ``breaker`` shares a circuit-breaker instance across pushes
    so one failing ingest path opens for both event types.
    """

    base_url: str
    # repr=False: the bearer must never reach logs or assertion diffs
    # via the default dataclass repr.
    token: str = field(repr=False)
    timeout_s: float = 3.0
    breaker: CircuitBreaker | None = None
    _client: httpx.AsyncClient | None = None
    # Events dropped while the breaker is open; reset on the next
    # successful push so each outage logs its own running total.
    _dropped: int = 0

    def __post_init__(self) -> None:
        if self.breaker is None:
            self.breaker = CircuitBreaker()

    async def _client_lazy(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                timeout=self.timeout_s,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: Mapping[str, Any]) -> None:
        client = await self._client_lazy()

        async def _do() -> None:
            response = await client.post(path, json=dict(payload))
            if response.status_code >= 400:
                raise IngestError(f"{path} → HTTP {response.status_code}")

        try:
            assert self.breaker is not None  # nosec B101 — type-narrowing
            await self.breaker.call(_do)
        except CircuitBreakerError:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                # Default-level breadcrumb: a silent debug line means an
                # open breaker discards telemetry with zero trace.
                logger.warning(
                    "telemetry breaker open, dropping push to %s "
                    "(%d events dropped since the breaker opened)",
                    path,
                    self._dropped,
                )
            else:
                logger.debug("telemetry breaker open, dropping push to %s", path)
        except IngestError as exc:
            logger.warning("telemetry ingest rejected: %s", exc)
        except httpx.HTTPError as exc:
            logger.warning("telemetry ingest transport error: %s", exc)
        else:
            if self._dropped:
                logger.info(
                    "telemetry breaker recovered; %d events were dropped during the outage",
                    self._dropped,
                )
                self._dropped = 0

    async def push_session(self, event: SessionEvent) -> None:
        """Push a session lifecycle event to C1."""
        await self._post("/ingest/session", _event_to_json(event))

    async def push_tool_use(self, event: ToolUseEvent) -> None:
        """Push a single tool-use observation to C1."""
        await self._post("/ingest/tool-use", _event_to_json(event))
