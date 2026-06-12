"""Observability — stdlib structured logging (PR-F F-3).

Zero external deps: pure standard library + json. Keeps M1 air-gap friendly
without dragging in OpenTelemetry / structlog ecosystems (those land later
once the customer ops side asks for distributed tracing).

Usage::

    from agent_platform_control.obs import setup_logging
    from agent_platform_control.config import get_settings

    s = get_settings()
    setup_logging(level=s.log_level, json_format=s.log_json)

Once set up, every ``logging.getLogger(...)`` call across the codebase
inherits the same handler + formatter. Extra context goes through
``logger.info("msg", extra={"request_id": ...})`` and lands in the JSON
payload alongside the standard fields.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO

from .request_id import current_request_id

# Standard logging record attributes — anything else passed via ``extra=...``
# survives onto the LogRecord. JsonFormatter picks those custom attrs up
# automatically by diffing against this set.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per log line, suitable for stdout → log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Correlation ID (H-12): stamp the current request's ID on every line
        # emitted while handling it. An explicit ``extra={"request_id": ...}``
        # wins (picked up below, where existing keys are skipped — so seed it
        # here only when the record doesn't carry its own).
        if not hasattr(record, "request_id"):
            request_id = current_request_id.get()
            if request_id is not None:
                payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Pick up ``extra={"...": ...}`` fields.
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def _build_handler(json_format: bool, stream: IO[str] | None) -> logging.Handler:
    handler = logging.StreamHandler(stream or sys.stderr)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        # Human-readable for dev. Mirrors uvicorn's default shape so the two
        # streams don't look wildly different when run side-by-side.
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    return handler


def setup_logging(
    *,
    level: str = "INFO",
    json_format: bool = False,
    stream: IO[str] | None = None,
) -> None:
    """Install one StreamHandler on the root logger; idempotent.

    Repeated calls replace the existing handler rather than stacking, so
    code paths that import ``setup_logging`` multiple times (e.g. uvicorn
    reload in dev) don't cause duplicate log lines.
    """
    root = logging.getLogger()

    # Clear any previously-installed handlers — both ours and uvicorn's
    # defaults (the FastAPI factory may have wired its own). One source of
    # truth.
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(_build_handler(json_format=json_format, stream=stream))
    root.setLevel(level.upper())
