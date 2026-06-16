"""Approval-driven redeploy helper (PR-D, decision 7).

Decision 7 (locked 2026-05-28): admins can re-trigger a deployment for a
given approval via ``POST /v1/deployments/from-approval/{id}?force=true``.
The ``force=true`` semantic is "destroy whatever I provisioned last time
for this approval and provision fresh".

This module implements the destroy half. The ``?force=true`` parameter
wiring on the endpoint lives in PR #129's from-approval handler — a
~10-line follow-up after both PRs land bridges the two.

The helper is **store-aware**: if a :class:`VmSecretStore` is provided
(PR-C runtime store), per-VM secrets are revoked alongside the VM
destruction so the new deployment provisions cleanly without leftover
ghost secrets.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import Deployment, DeploymentItem
from .audit import record_audit, sanitize
from .protocol import Provisioner

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def find_deployment_for_approval(
    session: AsyncSession, approval_request_id: int
) -> Deployment | None:
    """Reverse lookup: which deployment was created from this approval?

    Uses the ``approval_request_id`` reverse link in ``Deployment.extra``
    that the from-approval endpoint stamps at provision time (PR #129).
    Returns the latest live (non-cancelled) match, or None if none exists yet.
    """
    stmt = (
        select(Deployment)
        .where(
            Deployment.extra["approval_request_id"].as_integer() == approval_request_id,
            Deployment.state != "cancelled",
        )
        .order_by(Deployment.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().first()


async def _audit_redeploy_destroy(
    sm: async_sessionmaker[AsyncSession],
    item_id: int,
    vm_id: str,
    approval_request_id: int,
    *,
    result: str,
    error: Exception | None = None,
) -> None:
    """Own short txn: record one ``vm.destroy`` audit row (harness #H-6)."""
    params: dict[str, Any] = {
        "deployment_item_id": item_id,
        "approval_request_id": approval_request_id,
    }
    if error is not None:
        params["error"] = sanitize(str(error))
    async with sm() as session, session.begin():
        record_audit(
            session,
            actor="approval:redeploy",
            operation="vm.destroy",
            resource=vm_id,
            params=params,
            result=result,
        )


async def revoke_deployment_for_approval(
    sm: async_sessionmaker[AsyncSession],
    provisioner: Provisioner,
    approval_request_id: int,
    *,
    secret_store: Any | None = None,
) -> bool:
    """Decommission the existing deployment chained to the given approval.

    Steps (idempotent, best-effort per item):

    1. Find the existing deployment via the approval_request_id reverse link
    2. Mark the deployment + every item ``cancelled`` and free the approval slot
    3. For every item with a bound vm_id: ``provisioner.destroy_vm``
    4. For every item: if ``secret_store`` is provided, call
       ``revoke_per_vm_secrets`` (PR-C helper, dynamically imported to
       avoid a circular import when this module is reused without C18)

    Returns True if a deployment was revoked, False if none existed.

    AC1 (#353): the destroy IO runs with **no DB transaction held**. Phase 1 (a
    single short txn) marks everything cancelled and commits; phase 2 destroys
    each VM untransacted and audits each outcome in its own short txn. The old
    version held the caller's (HTTP request) write txn open across every destroy.

    Failures on a single VM (vCenter blip, missing pyVmomi) are logged and
    skipped — the deployment is already marked cancelled, so the operator's
    retry isn't blocked. The cleanup cron (decision 5) will sweep the leftover
    VM on a later pass.
    """
    # Phase 1: cancel + snapshot the destroy targets, then commit — releasing
    # the connection before any slow vCenter IO.
    async with sm() as session, session.begin():
        existing = await find_deployment_for_approval(session, approval_request_id)
        if existing is None:
            return False

        items_stmt = select(DeploymentItem).where(DeploymentItem.deployment_id == existing.id)
        items = (await session.execute(items_stmt)).scalars().all()
        targets: list[tuple[int, str | None]] = [(item.id, item.vm_id) for item in items]

        now = _utcnow()
        for item in items:
            item.state = "cancelled"
            item.updated_at = now
        existing.state = "cancelled"
        existing.updated_at = now
        # Release the UNIQUE approval slot so this approval can be re-provisioned
        # (PR-review I1); the audit link stays in `extra`.
        existing.approval_request_id = None
        dep_id = existing.id

    # Phase 2: destroy each bound VM with no txn held; audit + revoke secrets.
    for item_id, vm_id in targets:
        if vm_id is not None:
            try:
                await provisioner.destroy_vm(vm_id)
                await _audit_redeploy_destroy(
                    sm, item_id, vm_id, approval_request_id, result="success"
                )
            except Exception as exc:
                logger.exception(
                    "redeploy: destroy_vm failed for item %s vm_id=%s; leaving for cleanup cron",
                    item_id,
                    vm_id,
                )
                await _audit_redeploy_destroy(
                    sm, item_id, vm_id, approval_request_id, result="failure", error=exc
                )

        if secret_store is not None:
            # Late import keeps approval_redeploy importable without agent_platform_secrets.
            # In prod the caller already has both; this is purely defensive.
            try:
                from .secret_provisioner import revoke_per_vm_secrets

                await revoke_per_vm_secrets(secret_store, deployment_item_id=item_id)
            except Exception:
                logger.exception(
                    "redeploy: per-VM secret revoke failed for item %s; continuing",
                    item_id,
                )

    logger.info(
        "redeploy: revoked deployment %s (approval %s) with %s items",
        dep_id,
        approval_request_id,
        len(targets),
    )
    return True
