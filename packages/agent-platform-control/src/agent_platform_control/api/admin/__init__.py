"""/admin/* subpackage — RBAC-protected admin surface.

Refactored from the previous single-file ``api/admin.py`` per
docs/architecture/21-admin-console-redesign §3. Each sub-module
declares its own ``APIRouter`` and registers it on the package-level
``router`` so consumers (``app.py``) keep importing ``api.admin``
unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...auth import require_admin
from . import components, events, inventory, overview, topology, vcenters

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.include_router(inventory.router)
router.include_router(overview.router)
router.include_router(vcenters.router)
router.include_router(components.router)
router.include_router(events.router)
router.include_router(topology.router)

__all__ = ["router"]
