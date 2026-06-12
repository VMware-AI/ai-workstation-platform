"""/admin/vms/topology — graph payload for the Overview circular topology chart (W-2).

M1 shape: single vCenter (from env, same source as ``/admin/vcenters``)
in the center, every VM in C1's DB as a leaf node connected by one edge.
Per-tenant grouping and multi-vCenter rings land in M2.

Response payload is kept thin so the frontend can feed it straight into
echarts ``graph`` series without a transform layer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings_fresh
from ...db.models import VM
from ...db.session import get_session

router = APIRouter()


def _vcenter_node_name() -> str:
    """Match the ``name="default"`` convention used by /admin/vcenters.

    Returns ``"vcenter:default"`` when env-configured, or
    ``"vcenter:unconfigured"`` so the chart still renders a center even
    on a fresh dev DB. The string is the node ``id``, not a display label.
    """
    host = get_settings_fresh().vsphere_url
    return "vcenter:default" if host else "vcenter:unconfigured"


@router.get("/vms/topology")
async def vms_topology(session: AsyncSession = Depends(get_session)) -> dict:
    """Return ``{nodes: [...], edges: [...]}`` for the Overview chart.

    Node shape:
        - ``id``: stable handle (``vcenter:default`` or ``vm:<vm_id>``)
        - ``name``: display label (vCenter host or VM name)
        - ``category``: ``"vcenter"`` or ``"vm"``
        - ``state``: vCenter has ``"online"`` (placeholder until R-3 probe);
          VMs use their DB ``state`` field (provisioning|running|stopping|retired)
        - ``tenant``: tenant_id for VMs, ``null`` for vCenter

    Edge shape: ``{source: <vcenter_id>, target: <vm_id>}``. M1 single
    vCenter means every VM connects to the same center.
    """
    vcenter_id = _vcenter_node_name()
    vcenter_label = get_settings_fresh().vsphere_url or "unconfigured"

    vms = (await session.execute(select(VM))).scalars().all()

    nodes: list[dict] = [
        {
            "id": vcenter_id,
            "name": vcenter_label,
            "category": "vcenter",
            "state": "online",
            "tenant": None,
        }
    ]
    edges: list[dict] = []
    for vm in vms:
        vm_node_id = f"vm:{vm.id}"
        nodes.append(
            {
                "id": vm_node_id,
                "name": vm.name,
                "category": "vm",
                "state": vm.state,
                "tenant": vm.tenant_id,
            }
        )
        edges.append({"source": vcenter_id, "target": vm_node_id})

    return {"nodes": nodes, "edges": edges}
