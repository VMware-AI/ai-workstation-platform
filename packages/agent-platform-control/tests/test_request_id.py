"""Correlation/request-ID middleware + log injection (harness H-12, #213).

Every request gets an X-Request-ID — taken from the caller when it sends a
sane one, generated otherwise — echoed on the response and auto-injected
into every JSON log line emitted while handling that request.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest
from agent_platform_control.app import create_app
from agent_platform_control.obs import JsonFormatter
from agent_platform_control.request_id import (
    REQUEST_ID_HEADER,
    current_request_id,
    normalize_request_id,
    outbound_headers,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ----- normalize_request_id (pure) ---------------------------------------


def test_normalize_accepts_sane_id():
    assert normalize_request_id("req-abc_123.DEF") == "req-abc_123.DEF"


def test_normalize_rejects_control_chars():
    assert normalize_request_id("abc\ndef") is None


def test_normalize_rejects_overlong():
    assert normalize_request_id("a" * 129) is None


def test_normalize_rejects_empty_and_none():
    assert normalize_request_id("") is None
    assert normalize_request_id(None) is None


# ----- middleware ----------------------------------------------------------


@pytest.mark.anyio
async def test_response_carries_generated_request_id(client):
    resp = await client.get("/healthz")
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid
    assert normalize_request_id(rid) == rid  # generated IDs are sane


@pytest.mark.anyio
async def test_incoming_request_id_is_echoed(client):
    resp = await client.get("/healthz", headers={REQUEST_ID_HEADER: "caller-id-42"})
    assert resp.headers[REQUEST_ID_HEADER] == "caller-id-42"


@pytest.mark.anyio
async def test_garbage_incoming_id_is_replaced(client):
    resp = await client.get("/healthz", headers={REQUEST_ID_HEADER: "x" * 500})
    rid = resp.headers[REQUEST_ID_HEADER]
    assert rid != "x" * 500
    assert normalize_request_id(rid) == rid


@pytest.mark.anyio
async def test_logs_during_request_carry_request_id():
    """A log emitted inside a handler lands with the request's ID."""
    app = create_app()
    test_logger = logging.getLogger("test_request_id.handler")

    @app.get("/_test-log")
    async def _test_log():  # pragma: no cover — exercised via client below
        test_logger.info("inside handler")
        return {"ok": True}

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/_test-log", headers={REQUEST_ID_HEADER: "trace-me-7"})
        assert resp.status_code == 200
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert lines, "expected the handler to log at least one line"
        assert all(rec.get("request_id") == "trace-me-7" for rec in lines)
    finally:
        test_logger.removeHandler(handler)


@pytest.mark.anyio
async def test_every_request_emits_one_access_log_with_request_id(client):
    """The middleware itself logs one structured line per request, so even
    handlers that log nothing (healthz) are grep-able by request ID."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    access_logger = logging.getLogger("agent_platform_control.access")
    access_logger.addHandler(handler)
    access_logger.setLevel(logging.INFO)
    try:
        resp = await client.get("/healthz", headers={REQUEST_ID_HEADER: "access-id-5"})
        assert resp.status_code == 200
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert len(lines) == 1
        rec = lines[0]
        assert rec["request_id"] == "access-id-5"
        assert rec["method"] == "GET"
        assert rec["path"] == "/healthz"
        assert rec["status"] == 200
    finally:
        access_logger.removeHandler(handler)


@pytest.mark.anyio
async def test_unhandled_exception_still_emits_access_log_with_request_id():
    """Crashes are the requests one most wants to grep (review H-1): the
    access line must be emitted even when the handler raises, with the
    500 the outer ServerErrorMiddleware will produce."""
    app = create_app()

    @app.get("/_test-boom")
    async def _test_boom():  # pragma: no cover — exercised via client below
        raise RuntimeError("boom")

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    access_logger = logging.getLogger("agent_platform_control.access")
    access_logger.addHandler(handler)
    access_logger.setLevel(logging.INFO)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with pytest.raises(RuntimeError, match="boom"):
                await c.get("/_test-boom", headers={REQUEST_ID_HEADER: "crash-id-1"})
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert len(lines) == 1
        assert lines[0]["request_id"] == "crash-id-1"
        assert lines[0]["status"] == 500
        assert lines[0]["path"] == "/_test-boom"
    finally:
        access_logger.removeHandler(handler)


# ----- formatter auto-injection -------------------------------------------


def test_json_formatter_injects_current_request_id():
    token = current_request_id.set("ctx-id-1")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["request_id"] == "ctx-id-1"
    finally:
        current_request_id.reset(token)


def test_json_formatter_explicit_extra_wins():
    token = current_request_id.set("ctx-id-2")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
        record.request_id = "explicit-id"
        payload = json.loads(JsonFormatter().format(record))
        assert payload["request_id"] == "explicit-id"
    finally:
        current_request_id.reset(token)


def test_json_formatter_no_request_id_outside_request():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    payload = json.loads(JsonFormatter().format(record))
    assert "request_id" not in payload


# ----- outbound propagation ------------------------------------------------


def test_outbound_headers_inside_request_context():
    token = current_request_id.set("out-id-9")
    try:
        assert outbound_headers() == {REQUEST_ID_HEADER: "out-id-9"}
    finally:
        current_request_id.reset(token)


def test_outbound_headers_empty_outside_request_context():
    assert outbound_headers() == {}


@pytest.mark.anyio
async def test_component_probe_propagates_request_id():
    """control→C5 probes carry the current request's X-Request-ID."""
    import httpx
    from agent_platform_control.api.admin.components import _probe

    seen: dict[str, str] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"version": "x"})

    token = current_request_id.set("probe-id-3")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as c:
            result = await _probe(c, "http://c5", "/health/readiness")
    finally:
        current_request_id.reset(token)
    assert result["status"] == "ok"
    assert seen.get("x-request-id") == "probe-id-3"
