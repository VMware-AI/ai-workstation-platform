"""/admin/upgrades — blue-green upgrade control plane (Task 1.12 skeleton).

Endpoints (all admin-only):

    POST   /admin/upgrades/plan             build an UpgradePlan (no DB write)
    POST   /admin/upgrades/start            persist the plan + insert blue/green rows
    GET    /admin/upgrades/{id}             current state + per-VM status
    POST   /admin/upgrades/{id}/cutover     promote blue (requires confirm=True)
    POST   /admin/upgrades/{id}/rollback    tear down blue, keep green
    POST   /admin/upgrades/{id}/cleanup     delete green VMs (default dry_run=True)

Safety:
  - cutover / rollback / cleanup all reject ``confirm=False`` with HTTP 409 +
    a hint that includes the exact body field to set. Mirrors CLAUDE.md rule
    "破坏性操作必须双重确认 + dry-run".
  - cleanup defaults ``dry_run=True``: returns the list of green VMs that
    WOULD be retired without touching state.

Real vCenter calls are stubbed in ``UpgradeWorker``; this router only walks
the state machine + persists rows. See TODOs there for follow-up PRs.

Tenant-scope bypass (Decision 14)
---------------------------------
All endpoints here are ``require_admin`` and may read across tenants. The
``tenant_id`` on the request body is admin-supplied, not derived from the
caller's identity, so :func:`db.tenant_scope.tenant_scope` does not apply.
Whitelisted in ``scripts/check_tenant_scope.sh``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, require_admin
from ..db.models import Tenant, Upgrade, UpgradeVM, User
from ..db.session import get_session
from ..orchestrator import upgrade_state as us
from ..orchestrator.audit import record_audit
from ..orchestrator.upgrade_state import InvalidTransitionError, UpgradeState

# Marker for scripts/check_tenant_scope.sh — see module docstring.
TENANT_SCOPE_ADMIN_BYPASS = True

logger = logging.getLogger("agent_platform_control.api.upgrades")

router = APIRouter(prefix="/admin/upgrades", tags=["upgrades"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------- request schemas ----------


class UpgradeVMIn(BaseModel):
    """A single user's green→blue mapping submitted by the admin."""

    owner_id: str = Field(min_length=1, max_length=64)
    green_vm_id: str = Field(min_length=1, max_length=64)
    blue_intended_name: str = Field(min_length=1, max_length=255)


class UpgradePlanRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    from_version: str = Field(min_length=1, max_length=64)
    to_version: str = Field(min_length=1, max_length=64)
    vms: list[UpgradeVMIn] = Field(min_length=1, max_length=200)
    dry_run: bool = True


class UpgradeStartRequest(UpgradePlanRequest):
    """Same shape as plan — caller re-submits the (possibly tweaked) plan."""


class ConfirmBody(BaseModel):
    """Common shape for destructive endpoints. ``confirm`` MUST be True."""

    confirm: bool = False


class CleanupBody(ConfirmBody):
    dry_run: bool = True


# ---------- response schemas ----------


class UpgradePlanResponse(BaseModel):
    """Output of /plan — never persisted on its own."""

    tenant_id: str
    from_version: str
    to_version: str
    vm_count: int
    blue_vm_names: list[str]
    estimated_seconds: int
    home_volume_strategy: str
    dry_run: bool


class UpgradeVMOut(BaseModel):
    role: str
    vm_id: str | None
    intended_name: str
    owner_id: str
    status: str
    error_message: str | None


class UpgradeOut(BaseModel):
    id: str
    tenant_id: str
    from_version: str
    to_version: str
    state: str
    can_rollback: bool
    can_cutover: bool
    can_cleanup: bool
    is_terminal: bool
    failure_reason: str | None
    vms: list[UpgradeVMOut]
    created_at: datetime
    updated_at: datetime


# ---------- helpers ----------


def _serialize(up: Upgrade) -> UpgradeOut:
    state = UpgradeState(value=up.state)
    return UpgradeOut(
        id=up.id,
        tenant_id=up.tenant_id,
        from_version=up.from_version,
        to_version=up.to_version,
        state=up.state,
        can_rollback=state.can_rollback,
        can_cutover=state.can_cutover,
        can_cleanup=state.can_cleanup,
        is_terminal=state.is_terminal,
        failure_reason=up.failure_reason,
        vms=[
            UpgradeVMOut(
                role=v.role,
                vm_id=v.vm_id,
                intended_name=v.intended_name,
                owner_id=v.owner_id,
                status=v.status,
                error_message=v.error_message,
            )
            for v in up.vms
        ],
        created_at=up.created_at,
        updated_at=up.updated_at,
    )


def _estimate_seconds(vm_count: int) -> int:
    """Rough order-of-magnitude estimate; tightens up once real provisioner exists."""
    # 300s baseline per VM matches Task 1.12.1 strong success criterion
    # ("single user ≤ 5 minutes"). Concurrency factor handled by the worker.
    return 300 * vm_count


def _build_plan(req: UpgradePlanRequest) -> UpgradePlanResponse:
    blue_names = [v.blue_intended_name for v in req.vms]
    if len(set(blue_names)) != len(blue_names):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="duplicate blue_intended_name within batch",
        )
    return UpgradePlanResponse(
        tenant_id=req.tenant_id,
        from_version=req.from_version,
        to_version=req.to_version,
        vm_count=len(req.vms),
        blue_vm_names=blue_names,
        estimated_seconds=_estimate_seconds(len(req.vms)),
        home_volume_strategy="copy",  # R7: copy never move
        dry_run=req.dry_run,
    )


def _require_confirm(body: ConfirmBody, field_name: str = "confirm") -> None:
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"destructive operation requires explicit {field_name}=true "
                f"in the request body (double-confirm policy)"
            ),
        )


async def _load(session: AsyncSession, upgrade_id: str) -> Upgrade:
    up = await session.get(Upgrade, upgrade_id)
    if up is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"upgrade {upgrade_id!r} not found",
        )
    await session.refresh(up, attribute_names=["vms"])
    return up


def _audit(actor: str, op: str, upgrade_id: str, **extra: object) -> None:
    """Best-effort audit *log line* (non-destructive ops: plan / start /
    dry-run cleanup).

    Destructive ops (cutover / rollback / cleanup) additionally persist an
    ``AuditView`` row via :func:`orchestrator.audit.record_audit` in the same
    transaction as their state change — see the endpoints below (doc 34 H-6).
    """
    logger.info("audit op=%s upgrade=%s actor=%s extra=%s", op, upgrade_id, actor, extra)


# ---------- endpoints ----------


@router.post("/plan", response_model=UpgradePlanResponse)
async def plan_upgrade(
    body: UpgradePlanRequest,
    user: CurrentUser = Depends(require_admin),
) -> UpgradePlanResponse:
    """Build a plan without persisting anything. Pure function of the request."""
    plan = _build_plan(body)
    _audit(user.user_id, "upgrade.plan", upgrade_id="-", tenant=body.tenant_id)
    return plan


@router.post("/start", response_model=UpgradeOut, status_code=status.HTTP_201_CREATED)
async def start_upgrade(
    body: UpgradeStartRequest,
    user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UpgradeOut:
    """Persist the plan + create blue/green UpgradeVM rows.

    The worker (when enabled) will pick this row up on its next tick.
    """
    if body.dry_run:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot start an upgrade with dry_run=true; submit dry_run=false",
        )
    plan = _build_plan(body)  # revalidates names

    # Validate FK targets before inserting so an unknown tenant/owner yields a
    # teaching 422 instead of an orphan row (SQLite) or raw 500 (PG) — PR #118.
    if await session.get(Tenant, body.tenant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"tenant_id {body.tenant_id!r} is not a known tenant",
        )
    for owner_id in dict.fromkeys(vm.owner_id for vm in body.vms):
        if await session.get(User, owner_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"owner_id {owner_id!r} is not a known user",
            )

    upgrade_id = uuid.uuid4().hex
    now = _utcnow()
    up = Upgrade(
        id=upgrade_id,
        tenant_id=body.tenant_id,
        from_version=body.from_version,
        to_version=body.to_version,
        state=us.PLANNED,
        plan_json=plan.model_dump(),
        started_by=user.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(up)
    for vm in body.vms:
        # Green row: existing VM, will be retired on cleanup.
        session.add(
            UpgradeVM(
                upgrade_id=upgrade_id,
                role="green",
                vm_id=vm.green_vm_id,
                intended_name=vm.green_vm_id,
                owner_id=vm.owner_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        # Blue row: to be provisioned by the worker.
        session.add(
            UpgradeVM(
                upgrade_id=upgrade_id,
                role="blue",
                vm_id=None,
                intended_name=vm.blue_intended_name,
                owner_id=vm.owner_id,
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )

    await session.commit()
    await session.refresh(up, attribute_names=["vms"])
    _audit(user.user_id, "upgrade.start", upgrade_id, vm_count=len(body.vms))
    return _serialize(up)


@router.get("/{upgrade_id}", response_model=UpgradeOut)
async def get_upgrade(
    upgrade_id: str,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UpgradeOut:
    up = await _load(session, upgrade_id)
    return _serialize(up)


@router.post("/{upgrade_id}/cutover", response_model=UpgradeOut)
async def cutover_upgrade(
    upgrade_id: str,
    body: ConfirmBody,
    user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UpgradeOut:
    """Promote blue side to receive user traffic. Point of no return for rollback."""
    _require_confirm(body)
    up = await _load(session, upgrade_id)
    try:
        new_state = UpgradeState(value=up.state).cutover()
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    up.state = new_state.value
    up.updated_at = _utcnow()
    # Same transaction as the state change: audit row and cutover commit
    # together or not at all (doc 34 H-6).
    record_audit(
        session,
        actor=user.user_id,
        operation="upgrade.cutover",
        resource=f"upgrade:{upgrade_id}",
    )
    await session.commit()
    _audit(user.user_id, "upgrade.cutover", upgrade_id)
    return _serialize(up)


@router.post("/{upgrade_id}/rollback", response_model=UpgradeOut)
async def rollback_upgrade(
    upgrade_id: str,
    body: ConfirmBody,
    user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UpgradeOut:
    """Discard blue side, keep users on green. Only safe before cutover_done."""
    _require_confirm(body)
    up = await _load(session, upgrade_id)
    try:
        new_state = UpgradeState(value=up.state).rollback()
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    up.state = new_state.value
    up.updated_at = _utcnow()
    # Mark blue VMs torn-down; the worker stub will handle real delete later.
    for vm in up.vms:
        if vm.role == "blue":
            vm.status = "torn_down"
            vm.updated_at = up.updated_at
    record_audit(
        session,
        actor=user.user_id,
        operation="upgrade.rollback",
        resource=f"upgrade:{upgrade_id}",
    )
    await session.commit()
    _audit(user.user_id, "upgrade.rollback", upgrade_id)
    return _serialize(up)


@router.post("/{upgrade_id}/cleanup", response_model=UpgradeOut)
async def cleanup_upgrade(
    upgrade_id: str,
    body: CleanupBody,
    user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UpgradeOut:
    """Schedule green VM retirement. Defaults to dry_run=true.

    With ``dry_run=true``: returns the current state untouched. Use this to
    verify the green VM list before destructively cleaning up.
    With ``dry_run=false`` AND ``confirm=true``: transition into
    ``cleanup_pending`` (worker will retire green VMs).
    """
    if body.dry_run:
        # Dry-run is non-destructive; confirm not required, just inspect.
        up = await _load(session, upgrade_id)
        _audit(user.user_id, "upgrade.cleanup.dry_run", upgrade_id)
        return _serialize(up)

    _require_confirm(body)
    up = await _load(session, upgrade_id)
    try:
        new_state = UpgradeState(value=up.state).cleanup()
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    up.state = new_state.value
    up.updated_at = _utcnow()
    record_audit(
        session,
        actor=user.user_id,
        operation="upgrade.cleanup",
        resource=f"upgrade:{upgrade_id}",
    )
    await session.commit()
    _audit(user.user_id, "upgrade.cleanup", upgrade_id)
    return _serialize(up)
