"""/admin/components/health — aggregated readiness across the platform.

Per docs/architecture/21 §4.3 (revised 2026-05-30): only HTTP services
expose ``/readyz`` for live probing. Library and CLI components show
static ``not_a_service`` labels so the UI tab can render a coherent
picture without forcing every package to also become a daemon.

R-2 ships the table-of-truth; R-2b adds the live ``/readyz`` on C5
(agent-platform-llm-gateway), which is the only library that will be probed
in M1. R-3+ may extend this to other services as they appear.
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal

import httpx
from fastapi import APIRouter

from ... import __version__
from ...request_id import outbound_headers

router = APIRouter()


# Static catalog: kind drives UI badge styling. ``url_env`` is checked
# at request time; absence renders ``unknown`` rather than a hard error
# so dev environments without a full stack still see a clean response.
# ``probe_path`` lets us reuse upstream health surfaces (e.g. LiteLLM
# ships ``/health/readiness`` natively, so we don't add a sidecar to C5).
ComponentKind = Literal["http", "library", "cli", "placeholder"]
_CATALOG: tuple[tuple[str, str, ComponentKind, str | None, str], ...] = (
    # (id, label, kind, url_env, probe_path)
    ("c1", "agent-platform-control", "http", None, "/readyz"),  # self
    (
        "c5",
        "agent-platform-llm-gateway",
        "http",
        "AGENT_PLATFORM_LLM_GATEWAY_URL",
        "/health/readiness",
    ),
    ("c6", "agent-platform-llm-runtime", "placeholder", None, ""),
    ("c7", "agent-platform-telemetry-shim", "library", None, ""),
    ("c18", "agent-platform-secrets", "library", None, ""),
    ("c19", "agent-platform-fileshare", "placeholder", None, ""),
    ("c20", "agent-platform-agent-adapter", "cli", None, ""),
)


async def _probe(client: httpx.AsyncClient, url: str, path: str) -> dict:
    """Hit ``<url><path>``. Failures map to a structured ``unreachable``."""
    try:
        # Propagate the caller's correlation ID downstream (H-12, #213).
        r = await client.get(f"{url.rstrip('/')}{path}", timeout=1.0, headers=outbound_headers())
        r.raise_for_status()
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {"status": "ok", "version": body.get("version"), "raw": body, "probed": path}
    except httpx.HTTPError as exc:
        return {"status": "unreachable", "error": str(exc), "probed": path}


@router.get("/components/health")
async def components_health() -> dict:
    """Return one row per known component. Live probe HTTP services; static
    labels for library/cli/placeholder components."""
    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        coros: dict[str, asyncio.Task[dict]] = {}
        for cid, label, kind, url_env, probe_path in _CATALOG:
            if cid == "c1":
                # Self — short-circuit, don't HTTP back to ourselves.
                results.append(
                    {
                        "id": cid,
                        "label": label,
                        "kind": kind,
                        "status": "ok",
                        "version": __version__,
                    }
                )
                continue
            if kind != "http":
                results.append(
                    {
                        "id": cid,
                        "label": label,
                        "kind": kind,
                        "status": "not_a_service",
                    }
                )
                continue
            # HTTP service — probe if URL configured, else mark unknown.
            url = os.environ.get(url_env) if url_env else None
            if not url:
                results.append(
                    {
                        "id": cid,
                        "label": label,
                        "kind": kind,
                        "status": "unknown",
                        "note": f"set ${url_env} to enable live probe",
                    }
                )
                continue
            coros[cid] = asyncio.create_task(_probe(client, url, probe_path))
        for cid, task in coros.items():
            probe = await task
            label = next(c[1] for c in _CATALOG if c[0] == cid)
            results.append({"id": cid, "label": label, "kind": "http", **probe})
    # Preserve catalog order (asyncio.create_task results came back out-of-order).
    by_id = {r["id"]: r for r in results}
    return {"components": [by_id[c[0]] for c in _CATALOG]}
