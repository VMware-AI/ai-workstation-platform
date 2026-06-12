"""C7 agent-platform-telemetry-shim — agent-neutral telemetry plumbing.

The shim exposes three things to downstream agent adapters
(claude-code / goose / qcoder, all built on top in tasks 1.7.2-1.7.4):

* ``TelemetryShim`` — Protocol every adapter implements
* ``IngestClient`` — HTTP push to C1 ``/ingest/session``
* ``CircuitBreaker`` — three-state breaker so ingest failures don't
  take down the agent runtime
"""

from .circuit_breaker import CircuitBreaker, CircuitBreakerError
from .events import SessionEvent, ToolUseEvent
from .ingest_client import IngestClient, IngestError
from .protocol import TelemetryShim

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerError",
    "IngestClient",
    "IngestError",
    "SessionEvent",
    "TelemetryShim",
    "ToolUseEvent",
]
