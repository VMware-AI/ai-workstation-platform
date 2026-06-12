"""Unit tests for UsageCallback — payload shaping + error tolerance.

Tests do NOT require litellm to be installed; the callback module guards
the CustomLogger import so it falls back to object().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from agent_platform_llm_gateway.callbacks import (
    UsageCallback,
    _build_event,
    _extract_usage,
    _extract_user,
)


def _kwargs_with_user(
    user_id="alice", tenant_id="t-a", agent="claude-code", model="qwen-coder-32b"
):
    return {
        "model": model,
        "litellm_params": {
            "metadata": {"user_id": user_id, "tenant_id": tenant_id, "agent": agent},
        },
    }


def _usage_obj(prompt=100, completion=50):
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    )


# ----- pure-function helpers -------------------------------------------------


def test_extract_user_from_metadata():
    user_id, tenant_id, agent = _extract_user(_kwargs_with_user("bob", "t-b", "goose"))
    assert (user_id, tenant_id, agent) == ("bob", "t-b", "goose")


def test_extract_user_falls_back_to_top_level_user():
    # OpenAI-style top-level 'user' field, no metadata
    user_id, tenant_id, _ = _extract_user({"user": "carol"})
    assert user_id == "carol"
    assert tenant_id == "unknown"


def test_extract_user_returns_unknown_when_nothing_set():
    # 1.5.3 acceptance: payload must surface the missing-user case loudly,
    # not silently pretend everything is fine.
    user_id, tenant_id, agent = _extract_user({})
    assert user_id == "unknown"
    assert tenant_id == "unknown"
    assert agent == "claude-code"


def test_extract_usage_from_namespace():
    in_tokens, out_tokens = _extract_usage(_usage_obj(200, 75))
    assert (in_tokens, out_tokens) == (200, 75)


def test_extract_usage_from_dict():
    in_tokens, out_tokens = _extract_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 11}})
    assert (in_tokens, out_tokens) == (7, 11)


def test_extract_usage_zero_on_missing():
    assert _extract_usage(SimpleNamespace()) == (0, 0)
    assert _extract_usage({}) == (0, 0)


def test_build_event_shape_matches_c1_schema():
    ev = _build_event(_kwargs_with_user(), _usage_obj(120, 45), duration_s=1.5)
    assert set(ev.keys()) == {
        "user_id",
        "tenant_id",
        "agent",
        "model",
        "in_tokens",
        "out_tokens",
        "duration_s",
        "correlation_id",
    }
    assert ev["user_id"] == "alice"
    assert ev["in_tokens"] == 120
    assert ev["out_tokens"] == 45
    assert ev["duration_s"] == 1.5


def test_build_event_clamps_negative_duration():
    ev = _build_event(_kwargs_with_user(), _usage_obj(), duration_s=-0.5)
    assert ev["duration_s"] == 0.0


def _kwargs_with_request_id(request_id="req-abc-123"):
    kw = _kwargs_with_user()
    kw["litellm_params"]["proxy_server_request"] = {"headers": {"x-request-id": request_id}}
    return kw


def test_build_event_includes_correlation_id_from_request_id():
    # G1 (doc 37 §4.4): thread the caller's x-request-id into the ingest event as
    # correlation_id so token_usage + audit join back to the originating call.
    ev = _build_event(_kwargs_with_request_id("req-abc-123"), _usage_obj(), duration_s=1.0)
    assert ev["correlation_id"] == "req-abc-123"


def test_build_event_correlation_id_none_when_absent():
    # No x-request-id header → correlation_id is None (C1 ingest accepts it).
    ev = _build_event(_kwargs_with_user(), _usage_obj(), duration_s=1.0)
    assert ev["correlation_id"] is None


# ----- async POST roundtrip (mocked httpx) -----------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(201, json={"id": 1})


@pytest.mark.asyncio
async def test_async_post_carries_user_id(monkeypatch):
    """1.5.3 acceptance: POST payload must contain the actual user_id."""
    transport = _MockTransport()

    # Patch AsyncClient default transport
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    cb = UsageCallback(ingest_url="http://test", service_token="t-token")
    start = datetime(2026, 5, 24, 6, 0, 0)
    end = start + timedelta(seconds=2)
    await cb.async_log_success_event(_kwargs_with_user("alice"), _usage_obj(10, 20), start, end)

    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.url.path == "/api/ingest/token-usage"
    assert req.headers["authorization"] == "Bearer t-token"
    payload = json.loads(req.content)
    assert payload["user_id"] == "alice"
    assert payload["in_tokens"] == 10
    assert payload["out_tokens"] == 20
    assert payload["duration_s"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_network_failure_does_not_raise(caplog):
    """A broken C1 must NOT crash the LLM serving path."""
    cb = UsageCallback(ingest_url="http://127.0.0.1:1", service_token="t-token", timeout_s=0.1)
    start = datetime(2026, 5, 24, 6, 0, 0)
    end = start + timedelta(seconds=0.1)
    with caplog.at_level(logging.WARNING, logger="agent_platform_llm_gateway.callbacks"):
        await cb.async_log_success_event(_kwargs_with_user(), _usage_obj(), start, end)
    assert any("usage ingest failed" in r.message for r in caplog.records)


def test_init_without_token_warns(caplog, monkeypatch):
    monkeypatch.delenv("C1_INGEST_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="agent_platform_llm_gateway.callbacks"):
        UsageCallback(ingest_url="http://test")
    assert any("without C1_INGEST_TOKEN" in r.message for r in caplog.records)
