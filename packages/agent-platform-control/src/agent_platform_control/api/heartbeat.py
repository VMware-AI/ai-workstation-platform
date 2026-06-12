"""/api/heartbeat — in-VM heartbeat (PR-D, decision 16).

A goose/qoder/etc. agent inside the VM POSTs here every minute or so with
its ``heartbeat_access_token`` (issued by ``secret_provisioner`` at clone
time, distributed via cloud-init token exchange in PR-C). The endpoint
hashes the bearer + looks up the owning ``DeploymentItem`` + updates
``vms.last_heartbeat_at``. The sweeper in ``cleanup_cron.HeartbeatSweeper``
reads that column to drive the 5min/30min unhealthy/lost transitions.

Auth shape mirrors ``api/cloud_init.exchange_token``: the bearer token
is hashed at the boundary (plaintext never persisted), missing bearers
or unknown hashes both return 401 so callers can't probe membership.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import VM, DeploymentItem
from ..db.session import get_session
from ..orchestrator.secret_provisioner import hash_heartbeat_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/heartbeat", tags=["heartbeat"])


class HeartbeatResponse(BaseModel):
    """Echo of what the server saw — useful for VM-side liveness logging."""

    vm_id: str | None
    deployment_item_id: int
    received_at: datetime


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


@router.post("", response_model=HeartbeatResponse)
async def report_heartbeat(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> HeartbeatResponse:
    """In-VM agent → control plane liveness signal.

    The endpoint is **idempotent within the millisecond** — repeated calls
    in the same second just keep stamping the same timestamp. Sweeper
    consults ``last_heartbeat_at`` periodically; missed reports widen the
    gap until the unhealthy/lost thresholds trip.
    """
    token = _bearer_token(authorization)
    token_hash = hash_heartbeat_token(token)

    stmt = select(DeploymentItem).where(DeploymentItem.heartbeat_token_hash == token_hash)
    item: DeploymentItem | None = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown heartbeat token",
        )

    now = datetime.now(UTC)

    # Update vm.last_heartbeat_at if the item is bound to a VM. An unbound
    # item (the worker hasn't stamped vm_id yet) shouldn't reach this code
    # in practice — the heartbeat agent only starts after exchange-token —
    # but we accept it gracefully rather than 500-ing.
    if item.vm_id is not None:
        vm = await session.get(VM, item.vm_id)
        if vm is not None:
            vm.last_heartbeat_at = now
            # A heartbeat is also evidence that an unhealthy/lost VM is back.
            # Promote back to running so the portal reflects reality.
            if vm.state in {"unhealthy", "lost"}:
                vm.state = "running"

    await session.commit()
    return HeartbeatResponse(
        vm_id=item.vm_id,
        deployment_item_id=item.id,
        received_at=now,
    )
