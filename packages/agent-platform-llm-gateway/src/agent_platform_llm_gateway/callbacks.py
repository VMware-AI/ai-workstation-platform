"""LiteLLM callback that ships each call's usage to C1.

Wires Task 1.5.3 (gateway-side user_id capture) + 1.5.4 (one TokenUsage
row per call). LiteLLM proxy resolves the caller's virtual_key, which
carries ``metadata.user_id`` / ``metadata.tenant_id`` / ``metadata.agent``
set by the operator in the YAML (see config.yaml.example). On every
successful (or failed) LLM call LiteLLM fires our callback; we extract
those fields and POST a single event to ``{C1_INGEST_URL}/api/ingest/token-usage``
using the shared service token (``C1_INGEST_TOKEN`` env var).

Network failures and unexpected payload shapes are logged but never
re-raised: a broken accounting pipeline must not take down the LLM
serving path. Acceptance "网关侧拿到 user_id" passes when the POST
payload contains a real ``user_id`` (not the ``unknown`` fallback).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

# Inherit from LiteLLM's CustomLogger when available so the proxy can load us
# via `litellm_settings.callbacks: ["agent_platform_llm_gateway.callbacks.UsageCallback"]`.
# Fall back to object() in unit-test envs where litellm isn't installed.
try:
    from litellm.integrations.custom_logger import CustomLogger as _Base  # type: ignore
except Exception:  # pragma: no cover — only hit in litellm-less envs

    class _Base:  # type: ignore[no-redef]
        pass


logger = logging.getLogger(__name__)

_DEFAULT_INGEST_URL = "http://localhost:8000"
_DEFAULT_TIMEOUT_S = 3.0
_UNKNOWN_USER = "unknown"
_UNKNOWN_TENANT = "unknown"
_UNKNOWN_AGENT = "claude-code"  # ingest enum default — operator should override


def _extract_user(kwargs: dict[str, Any]) -> tuple[str, str, str]:
    """Pull (user_id, tenant_id, agent) from LiteLLM kwargs.

    LiteLLM puts virtual-key metadata at ``kwargs['litellm_params']['metadata']``
    (see litellm/proxy/auth/user_api_key_auth.py). We also accept a top-level
    ``user`` for compatibility with OpenAI's user field.
    """
    params = kwargs.get("litellm_params") or {}
    meta = (params.get("metadata") if isinstance(params, dict) else None) or {}
    user_id = meta.get("user_id") or kwargs.get("user") or _UNKNOWN_USER
    tenant_id = meta.get("tenant_id") or _UNKNOWN_TENANT
    agent = meta.get("agent") or _UNKNOWN_AGENT
    return str(user_id), str(tenant_id), str(agent)


# Same conservative charset as control's request_id.normalize_request_id —
# an attacker-controlled header must not flow verbatim into logs/headers
# (harness H-12, #213).
_SANE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _extract_request_id(kwargs: dict[str, Any]) -> str | None:
    """Pull the caller's x-request-id from LiteLLM's proxy request, if sane.

    LiteLLM proxy exposes the inbound HTTP request at
    ``kwargs['litellm_params']['proxy_server_request']`` with a ``headers``
    dict. Header names may arrive in any casing.
    """
    params = kwargs.get("litellm_params") or {}
    proxy_req = (params.get("proxy_server_request") if isinstance(params, dict) else None) or {}
    headers = (proxy_req.get("headers") if isinstance(proxy_req, dict) else None) or {}
    if not isinstance(headers, dict):
        return None
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == "x-request-id" and isinstance(value, str):
            return value if _SANE_REQUEST_ID.match(value) else None
    return None


def _extract_usage(response_obj: Any) -> tuple[int, int]:
    """Pull (in_tokens, out_tokens) from a LiteLLM response.

    Tolerates dict, ModelResponse, or anything with a ``usage`` attribute
    or key. Returns (0, 0) on shape mismatch (still ships the event so the
    timeline isn't gappy, and we can spot bad backends by tokens=0 rows).
    """
    usage: Any = None
    if isinstance(response_obj, dict):
        usage = response_obj.get("usage")
    else:
        usage = getattr(response_obj, "usage", None)
    if usage is None:
        return 0, 0
    get = (
        (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    )
    return int(get("prompt_tokens") or 0), int(get("completion_tokens") or 0)


def _build_event(kwargs: dict[str, Any], response_obj: Any, duration_s: float) -> dict[str, Any]:
    user_id, tenant_id, agent = _extract_user(kwargs)
    in_tokens, out_tokens = _extract_usage(response_obj)
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "agent": agent,
        "model": str(kwargs.get("model") or "unknown"),
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "duration_s": max(duration_s, 0.0),
        # G1 (doc 37 §4.4): the caller's x-request-id becomes the correlation_id
        # so token_usage + audit join back to the originating call. None when the
        # caller sent no (sane) x-request-id — C1 ingest accepts that.
        "correlation_id": _extract_request_id(kwargs),
    }


def _duration(start_time: Any, end_time: Any) -> float:
    try:
        return float((end_time - start_time).total_seconds())
    except Exception:
        return 0.0


class UsageCallback(_Base):
    """Ship one TokenUsage row per LLM call to C1.

    LiteLLM loads this via dotted-path in config. The proxy instantiates
    it once and reuses across requests, so the httpx client is created
    lazily and reused — but failures fall back to per-call clients so a
    transient pool exhaustion never silently drops events.
    """

    def __init__(
        self,
        ingest_url: str | None = None,
        service_token: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        super().__init__()
        self.ingest_url = (
            ingest_url or os.environ.get("C1_INGEST_URL", _DEFAULT_INGEST_URL)
        ).rstrip("/")
        self.service_token = service_token or os.environ.get("C1_INGEST_TOKEN", "")
        self.timeout_s = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
        if not self.service_token:
            logger.warning(
                "UsageCallback initialized without C1_INGEST_TOKEN; events will be dropped 401."
            )

    # ----- sync LiteLLM hooks ---------------------------------------------

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._post(
            _build_event(kwargs, response_obj, _duration(start_time, end_time)),
            _extract_request_id(kwargs),
        )

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        # Failed call: keep the row (0/0 tokens) so the timeline still
        # records the attempt — debugging "where did my budget go" needs it.
        self._post(
            _build_event(kwargs, response_obj, _duration(start_time, end_time)),
            _extract_request_id(kwargs),
        )

    # ----- async LiteLLM hooks --------------------------------------------

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        await self._apost(
            _build_event(kwargs, response_obj, _duration(start_time, end_time)),
            _extract_request_id(kwargs),
        )

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        await self._apost(
            _build_event(kwargs, response_obj, _duration(start_time, end_time)),
            _extract_request_id(kwargs),
        )

    # ----- transport ------------------------------------------------------

    def _headers(self, request_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "Content-Type": "application/json",
        }
        # Correlation (H-12, #213): forward the caller's request ID so the
        # same X-Request-ID greps across gateway and C1 logs.
        if request_id:
            headers["X-Request-ID"] = request_id
        return headers

    def _post(self, event: dict[str, Any], request_id: str | None = None) -> None:
        try:
            with httpx.Client(timeout=self.timeout_s) as c:
                r = c.post(
                    f"{self.ingest_url}/api/ingest/token-usage",
                    json=event,
                    headers=self._headers(request_id),
                )
                if r.status_code >= 400:
                    logger.warning(
                        "usage ingest %s: %s (request_id=%s)",
                        r.status_code,
                        r.text[:200],
                        request_id or "-",
                    )
        except Exception as exc:
            logger.warning("usage ingest failed: %s (request_id=%s)", exc, request_id or "-")

    async def _apost(self, event: dict[str, Any], request_id: str | None = None) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(
                    f"{self.ingest_url}/api/ingest/token-usage",
                    json=event,
                    headers=self._headers(request_id),
                )
                if r.status_code >= 400:
                    logger.warning(
                        "usage ingest %s: %s (request_id=%s)",
                        r.status_code,
                        r.text[:200],
                        request_id or "-",
                    )
        except Exception as exc:
            logger.warning("usage ingest failed: %s (request_id=%s)", exc, request_id or "-")
