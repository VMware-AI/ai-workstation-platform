"""/admin/overview — single-call aggregate for the Overview dashboard.

Combines control-plane self-check + count snapshots from C1's own DB into
one response so the UI doesn't have to fan-out N times on page load.

vCenter status comes back as ``"deferred"`` here; the dedicated vcenters
endpoints (and R-3 wiring) provide the real probe.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import __version__
from ...db.models import VM, Deployment, TokenUsage
from ...db.session import get_session

router = APIRouter()


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict:
    """Aggregate KPI snapshot for the Overview tab.

    Counts are cheap aggregate queries. vCenter health is intentionally
    left as ``"deferred"`` here — R-3 wires it through the dedicated
    ``/admin/vcenters/{name}/health`` endpoint, where caching can be
    applied independently.
    """
    vm_count = await session.scalar(select(func.count()).select_from(VM))
    running_vm = await session.scalar(
        select(func.count()).select_from(VM).where(VM.state == "running")
    )
    deployment_count = await session.scalar(select(func.count()).select_from(Deployment))
    # "provisioning" is not in the Deployment state set (pending/running/
    # completed/partially_failed/failed/cancelled); count the active states.
    # Response key stays "deployments_provisioning" for UI compatibility.
    provisioning = await session.scalar(
        select(func.count())
        .select_from(Deployment)
        .where(Deployment.state.in_(("pending", "running")))
    )
    today_tokens = await session.scalar(
        select(func.coalesce(func.sum(TokenUsage.in_tokens + TokenUsage.out_tokens), 0)).where(
            TokenUsage.ts >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        )
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "control_plane": {"status": "ok", "version": __version__},
        "vcenter": {"status": "deferred", "note": "wired in R-3 via /admin/vcenters"},
        "runtime": {"status": "deferred", "note": "wired in R-3"},
        "counts": {
            "vms_total": int(vm_count or 0),
            "vms_running": int(running_vm or 0),
            "deployments_total": int(deployment_count or 0),
            "deployments_provisioning": int(provisioning or 0),
            "tokens_today": int(today_tokens or 0),
        },
    }
