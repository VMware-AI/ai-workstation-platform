"""Correlation-ID propagation through the gateway (harness H-12, #213).

Two legs:
- UsageCallback extracts the caller's ``x-request-id`` (LiteLLM exposes the
  proxy request at ``litellm_params.proxy_server_request``) and forwards it
  as ``X-Request-ID`` on the ingest POST, so the same ID lands in C1's logs.
- ``start`` defaults LiteLLM's own logs to JSON (``JSON_LOGS`` env) so the
  gateway side of the chain is grep-able too.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import yaml
from agent_platform_llm_gateway.callbacks import UsageCallback, _extract_request_id
from agent_platform_llm_gateway.cli import app
from typer.testing import CliRunner


def _kwargs_with_headers(headers: dict[str, str] | None) -> dict:
    kwargs: dict = {
        "model": "qwen-coder-32b",
        "litellm_params": {
            "metadata": {"user_id": "alice", "tenant_id": "t-a", "agent": "claude-code"},
        },
    }
    if headers is not None:
        kwargs["litellm_params"]["proxy_server_request"] = {"headers": headers}
    return kwargs


def _usage_obj():
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20))


# ----- _extract_request_id ---------------------------------------------------


def test_extracts_request_id_from_proxy_headers():
    kwargs = _kwargs_with_headers({"x-request-id": "req-abc-123"})
    assert _extract_request_id(kwargs) == "req-abc-123"


def test_extracts_request_id_case_insensitive():
    kwargs = _kwargs_with_headers({"X-Request-ID": "req-abc-123"})
    assert _extract_request_id(kwargs) == "req-abc-123"


def test_returns_none_when_no_proxy_request():
    assert _extract_request_id(_kwargs_with_headers(None)) is None


def test_returns_none_when_header_absent():
    assert _extract_request_id(_kwargs_with_headers({})) is None


def test_rejects_garbage_request_id():
    # Attacker-controlled header must not flow verbatim into logs/headers.
    assert _extract_request_id(_kwargs_with_headers({"x-request-id": "a\nb"})) is None
    assert _extract_request_id(_kwargs_with_headers({"x-request-id": "x" * 500})) is None


# ----- POST propagation ------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(201, json={"id": 1})


@pytest.mark.asyncio
async def test_async_post_forwards_request_id_header(monkeypatch):
    transport = _MockTransport()
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    cb = UsageCallback(ingest_url="http://test", service_token="t-token")
    start = datetime(2026, 6, 6, 6, 0, 0)
    await cb.async_log_success_event(
        _kwargs_with_headers({"x-request-id": "trace-42"}),
        _usage_obj(),
        start,
        start + timedelta(seconds=1),
    )

    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.headers["x-request-id"] == "trace-42"
    # Payload itself is unchanged — propagation rides the header.
    assert json.loads(req.content)["user_id"] == "alice"


@pytest.mark.asyncio
async def test_async_post_omits_header_when_no_request_id(monkeypatch):
    transport = _MockTransport()
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    cb = UsageCallback(ingest_url="http://test", service_token="t-token")
    start = datetime(2026, 6, 6, 6, 0, 0)
    await cb.async_log_success_event(
        _kwargs_with_headers(None), _usage_obj(), start, start + timedelta(seconds=1)
    )

    assert len(transport.requests) == 1
    assert "x-request-id" not in transport.requests[0].headers


@pytest.mark.asyncio
async def test_ingest_failure_log_carries_request_id(caplog):
    """Gateway-side grep-ability (review M-1): the wrapper's own ingest
    logs must include the correlation ID."""
    import logging

    cb = UsageCallback(ingest_url="http://127.0.0.1:1", service_token="t", timeout_s=0.1)
    start = datetime(2026, 6, 6, 6, 0, 0)
    with caplog.at_level(logging.WARNING, logger="agent_platform_llm_gateway.callbacks"):
        await cb.async_log_success_event(
            _kwargs_with_headers({"x-request-id": "trace-77"}),
            _usage_obj(),
            start,
            start + timedelta(seconds=1),
        )
    assert any("request_id=trace-77" in r.getMessage() for r in caplog.records)


# ----- cli start: JSON logs by default --------------------------------------


def _write_good_config(tmp_path) -> str:
    cfg = {
        "model_list": [
            {
                "model_name": "qwen-coder-32b",
                "litellm_params": {
                    "model": "openai/qwen-coder-32b",
                    "api_base": "http://localhost:8001/v1",
                    "api_key": "dummy",
                },
            },
        ],
        "general_settings": {
            "master_key": "sk-very-real-secret-token-xxxxxxxxxxxxxxxxxxxx",
            "database_url": "postgresql://agent-platform@localhost/agent_platform_gateway",
        },
        # rpm/tpm under litellm_settings are silently ignored by LiteLLM (#351).
        "litellm_settings": {"success_callback": ["postgres"]},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def test_start_defaults_litellm_json_logs(tmp_path, monkeypatch):
    captured: dict[str, str] = {}

    def fake_execvp(file, args):
        captured["JSON_LOGS"] = os.environ.get("JSON_LOGS", "")

    monkeypatch.delenv("JSON_LOGS", raising=False)
    monkeypatch.setattr(os, "execvp", fake_execvp)
    result = CliRunner().invoke(app, ["start", "--config", _write_good_config(tmp_path)])
    assert result.exit_code == 0
    assert captured["JSON_LOGS"] == "True"


def test_start_respects_explicit_json_logs(tmp_path, monkeypatch):
    captured: dict[str, str] = {}

    def fake_execvp(file, args):
        captured["JSON_LOGS"] = os.environ.get("JSON_LOGS", "")

    monkeypatch.setenv("JSON_LOGS", "False")
    monkeypatch.setattr(os, "execvp", fake_execvp)
    result = CliRunner().invoke(app, ["start", "--config", _write_good_config(tmp_path)])
    assert result.exit_code == 0
    assert captured["JSON_LOGS"] == "False"
