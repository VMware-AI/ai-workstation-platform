"""Tests for agent_platform_control.obs — stdlib JSON logging setup (PR-F F-3)."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest
from agent_platform_control.obs import JsonFormatter, setup_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Each test starts with a clean root logger."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved[0])
    root.setLevel(saved[1])


def _capture(json_format: bool, level: str = "INFO") -> tuple[StringIO, logging.Logger]:
    buf = StringIO()
    setup_logging(level=level, json_format=json_format, stream=buf)
    return buf, logging.getLogger("test.captured")


def test_json_formatter_produces_valid_json():
    buf, log = _capture(json_format=True)
    log.info("hello %s", "world")

    line = buf.getvalue().strip()
    assert line  # non-empty
    parsed = json.loads(line)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.captured"
    assert "timestamp" in parsed


def test_json_formatter_includes_exception():
    buf, log = _capture(json_format=True)
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("oops")

    parsed = json.loads(buf.getvalue().strip().splitlines()[0])
    assert parsed["message"] == "oops"
    assert "exception" in parsed
    assert "ValueError: boom" in parsed["exception"]
    assert "Traceback" in parsed["exception"]


def test_human_formatter_is_readable():
    buf, log = _capture(json_format=False)
    log.info("hello human")
    out = buf.getvalue().strip()
    # Plain-text formatter — not JSON, but should still contain the message
    assert "hello human" in out
    assert "INFO" in out
    # Must NOT look like JSON (no leading brace)
    assert not out.startswith("{")


def test_log_level_filter_applies():
    buf, log = _capture(json_format=True, level="WARNING")
    log.info("invisible")
    log.warning("visible")
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["message"] == "visible"


def test_json_formatter_handles_extra_fields():
    """logger.info(..., extra={...}) lands in the JSON payload."""
    buf, log = _capture(json_format=True)
    log.info("with extras", extra={"request_id": "rq-1", "tenant_id": "t-a"})

    parsed = json.loads(buf.getvalue().strip().splitlines()[0])
    assert parsed["request_id"] == "rq-1"
    assert parsed["tenant_id"] == "t-a"


def test_setup_logging_is_idempotent():
    """Repeated calls don't double-handler the root logger."""
    setup_logging(level="INFO", json_format=False)
    setup_logging(level="INFO", json_format=False)
    setup_logging(level="DEBUG", json_format=True)

    root = logging.getLogger()
    assert len(root.handlers) == 1


def test_json_formatter_direct_use():
    """Smoke: the formatter class works standalone (no setup_logging needed)."""
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="msg",
        args=(),
        exc_info=None,
    )
    out = JsonFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["message"] == "msg"
    assert parsed["logger"] == "x"
