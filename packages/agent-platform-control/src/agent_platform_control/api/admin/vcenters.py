"""/admin/vcenters/* — read-only vCenter inventory surface.

M1 supports a single vCenter sourced from ``AGENT_PLATFORM_VCENTER_*`` env vars
(per docs/architecture/21 §4 revision). Multi-vCenter config.yaml lands
in M2.

R-2 locked the endpoint shapes; R-3 (this revision) replaces deferred
bodies with real ``vmware_aiops.ops.inventory`` calls. Inventory list
operations are pyVmomi-synchronous, so they run in a threadpool via
``asyncio.to_thread`` to keep the FastAPI event loop free.

Only ``vmware_aiops.ops.*`` is imported — never ``vmware_aiops.mcp_server``
which registers MCP tools at import (CLAUDE.md pitfall #32 / docs 21 §6).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, status

from ...orchestrator.vmware import _connect, _Target

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory inventory cache per docs/architecture/21 §3.3. 5-minute TTL
# protects vCenter from refresh-spam when admins open the tab.
_CACHE_TTL_S = 300.0
_INVENTORY_CACHE: dict[str, tuple[float, dict]] = {}


def _single_target() -> _Target | None:
    """Build a ``_Target`` from env. ``None`` if vCenter not configured."""
    host_raw = (
        os.environ.get("AGENT_PLATFORM_VCENTER_HOST")
        or os.environ.get("AGENT_PLATFORM_VSPHERE_HOST")
        or os.environ.get("AGENT_PLATFORM_VSPHERE_URL")
    )
    user = os.environ.get("AGENT_PLATFORM_VCENTER_USER") or os.environ.get(
        "AGENT_PLATFORM_VSPHERE_USER"
    )
    password = os.environ.get("AGENT_PLATFORM_VCENTER_PASSWORD") or os.environ.get(
        "AGENT_PLATFORM_VSPHERE_PASSWORD"
    )
    if not host_raw:
        return None

    # Accept either bare host or full URL.
    parsed = urlparse(host_raw if "://" in host_raw else f"https://{host_raw}")
    host = parsed.hostname or host_raw
    port = parsed.port or 443
    verify_ssl = os.environ.get("AGENT_PLATFORM_VCENTER_VERIFY_SSL", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    return _Target(
        host=host, port=port, user=user or "", password=password or "", verify_ssl=verify_ssl
    )


def _vc_descriptor() -> dict | None:
    """Public-safe descriptor for ``GET /vcenters`` — never includes the password."""
    t = _single_target()
    if t is None:
        return None
    return {
        "name": "default",
        "host": t.host,
        "port": t.port,
        "user": t.user,
        "verify_ssl": t.verify_ssl,
        "configured_via": "env",
        "note": "M1: single vCenter only; multi-vCenter via config.yaml lands in M2.",
    }


@router.get("/vcenters")
async def list_vcenters() -> dict:
    """List configured vCenters. M1 returns 1 entry from env, or empty list."""
    vc = _vc_descriptor()
    return {"vcenters": [vc] if vc else [], "_single_only": True}


def _ping_sync(target: _Target) -> dict:
    """Synchronously open (cached) SI + call RetrieveContent as a smoke test.

    Bypasses the per-target circuit breaker (H-14): this endpoint is the
    operator's diagnostic tool — it must show live vCenter state even while
    the provisioning breaker is open, and its failures must not count
    toward tripping it."""
    si = _connect(target, bypass_breaker=True)
    content = si.RetrieveContent()
    return {
        "status": "ok",
        "api_version": getattr(content.about, "apiVersion", None),
        "full_name": getattr(content.about, "fullName", None),
        "instance_uuid": getattr(content.about, "instanceUuid", None),
    }


@router.get("/vcenters/{name}/health")
async def vcenter_health(name: str) -> dict:
    """vCenter probe via pyVmomi SmartConnect (5s timeout). Returns ``status``
    plus minimal ``about`` fields on success, or a structured ``error``."""
    target = _single_target()
    if target is None or name != "default":
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"vCenter '{name}' not configured")
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_ping_sync, target), timeout=5.0)
        return {"name": name, "host": target.host, **info}
    except TimeoutError:
        return {
            "name": name,
            "host": target.host,
            "status": "timeout",
            "error": "vCenter ping exceeded 5s",
        }
    except Exception as exc:  # pyVmomi raises many concrete types; uniform surface here
        logger.warning("vcenter ping failed for %s: %s", target.host, exc)
        return {"name": name, "host": target.host, "status": "error", "error": str(exc)}


def _inventory_sync(target: _Target) -> dict[str, list[dict]]:
    """Pull lists synchronously via vmware-aiops ops layer."""
    # Import lazily so a missing vmware-aiops install fails on call, not on import.
    from vmware_aiops.ops.inventory import (
        list_clusters,
        list_datastores,
        list_hosts,
        list_networks,
    )

    si = _connect(target)
    return {
        "hosts": list_hosts(si),
        "clusters": list_clusters(si),
        "datastores": list_datastores(si),
        "networks": list_networks(si),
    }


def _cache_get(name: str) -> dict | None:
    entry = _INVENTORY_CACHE.get(name)
    if entry is None:
        return None
    ts, body = entry
    if time.time() - ts > _CACHE_TTL_S:
        _INVENTORY_CACHE.pop(name, None)
        return None
    return body


def _cache_put(name: str, body: dict) -> None:
    _INVENTORY_CACHE[name] = (time.time(), body)


@router.get("/vcenters/{name}/inventory")
async def vcenter_inventory(name: str, refresh: bool = False) -> dict:
    """Hosts / clusters / datastores / networks via ``vmware-aiops.ops.inventory``.

    5-minute in-memory cache (per docs 21 §3.3); pass ``?refresh=true`` to bust.
    """
    target = _single_target()
    if target is None or name != "default":
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"vCenter '{name}' not configured")

    if not refresh:
        cached = _cache_get(name)
        if cached is not None:
            return {"name": name, "cached": True, "ttl_s": _CACHE_TTL_S, **cached}

    try:
        body: dict[str, Any] = await asyncio.wait_for(
            asyncio.to_thread(_inventory_sync, target), timeout=15.0
        )
    except TimeoutError:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            f"vCenter '{name}' inventory call exceeded 15s",
        ) from None
    except Exception as exc:
        logger.warning("vcenter inventory failed for %s: %s", target.host, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"vCenter inventory error: {exc}") from exc

    body["counts"] = {k: len(v) for k, v in body.items() if isinstance(v, list)}
    _cache_put(name, body)
    return {"name": name, "cached": False, "ttl_s": _CACHE_TTL_S, **body}


def _templates_sync(target: _Target) -> list[dict]:
    """Return template-flagged VMs via vmware-aiops list_vms."""
    from vmware_aiops.ops.inventory import list_vms

    si = _connect(target)
    # ``list_vms`` returns ``{"vms": [...], "next_offset": ...}`` when compact;
    # we want raw rows for filtering.
    raw = list_vms(si, limit=None)
    rows = raw.get("vms", []) if isinstance(raw, dict) else raw
    # ``template`` field is exposed by vmware-aiops; fallback to ``config.template``
    # if structure differs across versions.
    return [r for r in rows if r.get("template") or r.get("is_template")]


@router.get("/vcenters/{name}/templates")
async def vcenter_templates(name: str) -> dict:
    """VM templates list via ``vmware-aiops.ops.inventory.list_vms`` filtered."""
    target = _single_target()
    if target is None or name != "default":
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"vCenter '{name}' not configured")

    try:
        templates = await asyncio.wait_for(asyncio.to_thread(_templates_sync, target), timeout=15.0)
    except TimeoutError:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            f"vCenter '{name}' templates call exceeded 15s",
        ) from None
    except Exception as exc:
        logger.warning("vcenter templates failed for %s: %s", target.host, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"vCenter templates error: {exc}") from exc

    return {"name": name, "templates": templates, "count": len(templates)}
