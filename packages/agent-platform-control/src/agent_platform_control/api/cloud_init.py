"""/api/cloud-init/* — in-VM secret bootstrap (Task 1.20.3).

Cloud-init inside a freshly-cloned VM gets the per-VM token via OVF
environment (injected at clone-time by the orchestrator). On first boot
the VM calls ``POST /api/cloud-init/exchange-token`` with that token as
a bearer credential; C1:

1. Looks up the DeploymentItem by ``sha256(token)`` (the plaintext token
   is never stored).
2. Single-use: refuses with 410 Gone if ``token_consumed_at`` is already
   set, so a leaked token can't be replayed after the legitimate boot.
3. Marks the item ``token_consumed_at = now()`` atomically.
4. Returns the secrets payload the VM needs to finish bootstrapping.

For M1 the payload is a deterministic stub (``vm_id``, ``owner_id``,
``deployment_id``, empty ``secrets`` dict). A follow-up wires the C18
SecretResolver to a per-template ``secret_refs`` list; the wire format
won't change, only the contents of ``secrets``.

Tenant-scope bypass (Decision 14)
---------------------------------
Authenticated by a per-VM single-use token (``sha256`` hash lookup), not a
user identity. The lookup is over ``DeploymentItem``, which has no
``tenant_id`` column (it's reached via the parent ``Deployment``). Token
hash is a global secret — adding a tenant filter would require the caller
to already know the tenant, which it can't. Whitelisted in
``scripts/check_tenant_scope.sh``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from agent_platform_secrets.vm_secret_store import (
    InMemoryVmSecretStore,
    VmSecretRef,
    VmSecretStore,
)
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Deployment, DeploymentItem
from ..db.session import get_session
from ..orchestrator.tokens import hash_user_token

# Marker for scripts/check_tenant_scope.sh — see module docstring.
TENANT_SCOPE_ADMIN_BYPASS = True

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cloud-init", tags=["cloud-init"])


# ----------------------------------------------------------------- store wiring
# M1 stub: a process-wide InMemoryVmSecretStore. Tests inject their own via
# FastAPI dependency_overrides. Prod swap to VaultwardenVmSecretStore is a
# one-line change in the factory below — the api/cloud_init.py handler does
# not touch a Vaultwarden client directly.

_default_store: VmSecretStore | None = None


def get_vm_secret_store() -> VmSecretStore:
    global _default_store
    if _default_store is None:
        _default_store = InMemoryVmSecretStore()
    return _default_store


def reset_vm_secret_store_for_tests() -> None:
    """Drop the process-wide store so the next call constructs a fresh one."""
    global _default_store
    _default_store = None


class ExchangeResponse(BaseModel):
    """Payload returned to the in-VM cloud-init script.

    The shape is stable; field semantics:

    - ``vm_id`` — the C1 VM id once the orchestrator has bound it
      (``None`` if the worker hasn't reached that state yet — cloud-init
      can still use ``deployment_item_id`` for logging).
    - ``deployment_item_id`` — the row id; useful for support tickets.
    - ``owner_id`` — the human owner from the users table; cloud-init
      uses this for the local UNIX account.
    - ``secrets`` — opaque ``{name: value}`` map. Empty until per-template
      ``secret_refs`` lands; documented so cloud-init scripts handle the
      empty case today instead of crashing.
    """

    vm_id: str | None
    deployment_item_id: int
    owner_id: str
    secrets: dict[str, Any]
    """M1 lock 2026-05-29: 4 entries.

    * ``llm_gateway_api_key`` (str)              — per-tenant; ``""`` if unset
    * ``registry_pull_creds`` (dict | None)      — per-tenant; ``{"username", "password"}``
                                                  or ``None`` when the customer
                                                  uses a public mirror
    * ``ssh_authorized_keys`` (str)              — per-VM; ``""`` if user has
                                                  not uploaded a pubkey
    * ``heartbeat_access_token`` (str)           — per-VM; always present
    """


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="empty bearer token",
        )
    return token


async def _secrets_for_item(
    item: DeploymentItem,
    *,
    tenant_id: str,
    store: VmSecretStore,
) -> dict[str, Any]:
    """Resolve the 4 M1 secrets this VM needs (decision 4 lock 2026-05-29).

    Missing entries degrade gracefully:
      * LLM key / SSH keys → empty string (VM still boots; LLM calls fail
        with a 401 at the gateway, SSH login is just unavailable)
      * heartbeat token   → empty string (heartbeat won't authenticate but
        nothing downstream relies on this being non-empty yet)
      * registry creds    → ``None`` (public mirror path)
    """
    scope = str(item.id)

    # Tenant-layer secrets need a non-empty tenant scope_id; if the parent
    # deployment is gone (tenant_id="") skip them and serve only the vm-layer
    # secrets, rather than letting VmSecretStore raise on an empty scope_id
    # (PR-review #135). vm-layer ssh/heartbeat still resolve off the item id.
    if tenant_id:
        llm_key = await store.get(VmSecretRef("tenant", tenant_id, "llm_gateway_api_key"))
        reg_creds_raw = await store.get(VmSecretRef("tenant", tenant_id, "registry_pull_creds"))
    else:
        llm_key = None
        reg_creds_raw = None
    ssh_keys = await store.get(VmSecretRef("vm", scope, "ssh_authorized_keys"))
    heartbeat = await store.get(VmSecretRef("vm", scope, "heartbeat_access_token"))

    # registry_pull_creds is stored as a JSON string ({"username","password"}).
    # Failing to parse means the store was hand-edited or corrupted — surface
    # as None and log, do not crash the entire exchange.
    reg_creds: dict[str, str] | None = None
    if reg_creds_raw:
        try:
            parsed = json.loads(reg_creds_raw)
            if isinstance(parsed, dict):
                reg_creds = parsed
            else:
                logger.warning(
                    "registry_pull_creds for tenant %s is not a JSON object; ignoring",
                    tenant_id,
                )
        except json.JSONDecodeError:
            logger.warning(
                "registry_pull_creds for tenant %s is not valid JSON; ignoring",
                tenant_id,
            )

    return {
        "llm_gateway_api_key": llm_key or "",
        "registry_pull_creds": reg_creds,
        "ssh_authorized_keys": ssh_keys or "",
        "heartbeat_access_token": heartbeat or "",
    }


@router.post("/exchange-token", response_model=ExchangeResponse)
async def exchange_token(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    store: VmSecretStore = Depends(get_vm_secret_store),
) -> ExchangeResponse:
    token = _bearer_token(authorization)
    token_hash = hash_user_token(token)

    stmt = select(DeploymentItem).where(DeploymentItem.user_token_hash == token_hash)
    item: DeploymentItem | None = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        # Same status as a missing bearer — never disclose whether a hash
        # value happens to exist in the DB.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown token",
        )

    # Single-use enforcement as an ATOMIC claim, not check-then-set: the
    # UPDATE only matches a still-unconsumed row, so two concurrent boots
    # racing the same token cannot both win (PR-review TOCTOU #126).
    item_id = item.id  # capture before any rollback expires the instance
    consumed_at = datetime.now(UTC)
    claim = await session.execute(
        update(DeploymentItem)
        .where(
            DeploymentItem.id == item_id,
            DeploymentItem.token_consumed_at.is_(None),
        )
        .values(token_consumed_at=consumed_at)
    )
    if claim.rowcount == 0:
        # Lost the race or a genuine replay — already consumed. Distinct
        # status so cloud-init can tell "wrong token" (401) from "already
        # booted" (410).
        await session.rollback()
        logger.warning("cloud-init token replay/race for deployment_item_id=%s", item_id)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="token already consumed",
        )

    # Look up tenant_id via the parent deployment — per-tenant secrets key
    # off it. If the deployment row is gone (FK should prevent, but defend
    # in depth) we fall back to "no tenant", giving an empty secrets payload
    # rather than 500ing.
    dep = await session.get(Deployment, item.deployment_id)
    tenant_id = dep.tenant_id if dep is not None else ""

    secrets = await _secrets_for_item(item, tenant_id=tenant_id, store=store)
    await session.commit()

    return ExchangeResponse(
        vm_id=item.vm_id,
        deployment_item_id=item.id,
        owner_id=item.owner_id,
        secrets=secrets,
    )
