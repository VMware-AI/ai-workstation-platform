"""Correlation/request-ID propagation (harness H-12, #213).

Pure-ASGI middleware: take the caller's ``X-Request-ID`` when it looks sane
(callers like web-ui or the C5 usage callback propagate theirs), otherwise
generate one. The ID lives in a ContextVar for the duration of the request
so ``obs.JsonFormatter`` stamps it onto every log line, and it is echoed on
the response so callers can correlate.

Incoming IDs are validated, not trusted: an attacker-controlled header that
lands verbatim in every log line is a log-injection vector, so anything
outside a conservative charset/length is replaced with a generated ID.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

_access_logger = logging.getLogger("agent_platform_control.access")

REQUEST_ID_HEADER = "X-Request-ID"
_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("latin-1")

# Conservative: enough for UUIDs and prefixed IDs, useless for injection.
_SANE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

_Message = dict[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]


def outbound_headers() -> dict[str, str]:
    """Headers to attach to outbound HTTP calls made while serving a request.

    Empty outside a request context (cron jobs, startup) — callers can
    always merge it unconditionally.
    """
    request_id = current_request_id.get()
    if request_id is None:
        return {}
    return {REQUEST_ID_HEADER: request_id}


def normalize_request_id(value: str | None) -> str | None:
    """Return the ID if it is safe to log verbatim, else None."""
    if value and _SANE_ID.match(value):
        return value
    return None


class RequestIdMiddleware:
    """Resolve the request ID, expose it via ContextVar, echo it on the response."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: _Message, receive: _Receive, send: _Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming: str | None = None
        for name, value in scope.get("headers", []):
            if name == _HEADER_BYTES:
                incoming = value.decode("latin-1")
                break
        request_id = normalize_request_id(incoming) or uuid4().hex
        status: dict[str, int] = {}

        async def send_with_header(message: _Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((_HEADER_BYTES, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        token = current_request_id.set(request_id)
        try:
            await self.app(scope, receive, send_with_header)
        finally:
            # One structured access line per request, emitted in ``finally``
            # so unhandled exceptions stay grep-able by request ID — crashes
            # are exactly the requests one greps for (PR #219 review H-1).
            # If the app raised before http.response.start, the outer
            # ServerErrorMiddleware turns it into a 500, hence the default.
            # Log BEFORE resetting the ContextVar so JsonFormatter still
            # stamps the ID.
            _access_logger.info(
                "request",
                extra={
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status.get("code", 500),
                },
            )
            current_request_id.reset(token)
