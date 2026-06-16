"""Per-user VM count quota (PR-E E-4 + E-5, decision 15).

Three-layer fallback:

  1. ``users.quota_vms``           (highest priority, per-user override)
  2. ``tenants.quota_vms_per_user`` (per-tenant default)
  3. ``DEFAULT_VMS_PER_USER`` = 3   (global M1 default)

Active VMs counted: anything not retired / lost / cleaned. Failed VMs are
counted so a noisy user can't bypass quota by triggering one bad deploy
per minute — admins still see the failed rows and can decommission via
the cleanup cron (decision 5) when they want to free the slot.
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import VM, Tenant, User

logger = logging.getLogger(__name__)


DEFAULT_VMS_PER_USER = 3

# Quota counts these states. Retired / lost / cleaned VMs are gone from
# the user's resource pool — counting them would punish recycling.
_ACTIVE_VM_STATES: tuple[str, ...] = (
    "provisioning",
    "running",
    "stopping",
    "unhealthy",
    "failed",
)


class QuotaExceededError(Exception):
    """Raised by ``check_user_vm_quota`` when an owner is at the limit.

    Carries enough context for the caller to produce an educational
    HTTPException without having to re-fetch the counts.
    """

    def __init__(self, *, owner_id: str, current: int, limit: int, extra_requested: int = 0):
        self.owner_id = owner_id
        self.current = current
        self.limit = limit
        self.extra_requested = extra_requested
        super().__init__(
            f"user {owner_id!r} has {current} active VMs (max {limit}); "
            f"refusing {extra_requested} additional request(s)"
        )


async def resolve_vm_quota_for_user(session: AsyncSession, user_id: str) -> int:
    """Walk the 3-layer fallback to find the active limit for ``user_id``."""
    user = await session.get(User, user_id)
    if user is None:
        # Unknown user. Defer the existence check to the caller (likely
        # FK enforcement elsewhere); for quota purposes treat as default.
        return DEFAULT_VMS_PER_USER

    if user.quota_vms is not None:
        return int(user.quota_vms)

    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is not None and tenant.quota_vms_per_user is not None:
        return int(tenant.quota_vms_per_user)

    return DEFAULT_VMS_PER_USER


async def count_active_vms_for_user(session: AsyncSession, user_id: str) -> int:
    stmt = (
        select(func.count(VM.id))
        .where(VM.owner_id == user_id)
        .where(VM.state.in_(_ACTIVE_VM_STATES))
    )
    return int((await session.execute(stmt)).scalar() or 0)


async def check_user_vm_quota(
    session: AsyncSession,
    *,
    owner_id: str,
    extra_requested: int = 1,
) -> None:
    """Raise :class:`QuotaExceededError` if granting ``extra_requested``
    additional VMs would exceed the active limit for ``owner_id``."""
    limit = await resolve_vm_quota_for_user(session, owner_id)
    current = await count_active_vms_for_user(session, owner_id)
    if current + extra_requested > limit:
        raise QuotaExceededError(
            owner_id=owner_id,
            current=current,
            limit=limit,
            extra_requested=extra_requested,
        )


async def resolve_vm_quotas_for_users(session: AsyncSession, user_ids: list[str]) -> dict[str, int]:
    """Batch form of :func:`resolve_vm_quota_for_user`.

    Resolves the 3-layer fallback for every id in ``user_ids`` using a single
    ``WHERE id IN (...)`` over users plus one over the tenants they reference —
    two queries total, regardless of the number of owners (no N+1). Unknown
    users resolve to ``DEFAULT_VMS_PER_USER`` (existence is enforced elsewhere).
    """
    unique_ids = list(dict.fromkeys(user_ids))
    if not unique_ids:
        return {}

    users = (await session.execute(select(User).where(User.id.in_(unique_ids)))).scalars().all()
    by_id = {u.id: u for u in users}

    # Only tenants for users that fall through to the tenant layer matter.
    tenant_ids = {u.tenant_id for u in users if u.quota_vms is None and u.tenant_id is not None}
    tenants_by_id: dict[str, Tenant] = {}
    if tenant_ids:
        tenants = (
            (await session.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))).scalars().all()
        )
        tenants_by_id = {t.id: t for t in tenants}

    resolved: dict[str, int] = {}
    for uid in unique_ids:
        user = by_id.get(uid)
        if user is None:
            resolved[uid] = DEFAULT_VMS_PER_USER
            continue
        if user.quota_vms is not None:
            resolved[uid] = int(user.quota_vms)
            continue
        tenant = tenants_by_id.get(user.tenant_id)
        if tenant is not None and tenant.quota_vms_per_user is not None:
            resolved[uid] = int(tenant.quota_vms_per_user)
            continue
        resolved[uid] = DEFAULT_VMS_PER_USER
    return resolved


async def count_active_vms_for_users(session: AsyncSession, user_ids: list[str]) -> dict[str, int]:
    """Batch form of :func:`count_active_vms_for_user`: one grouped query
    returns the active VM count per owner. Owners with zero active VMs are
    absent from the result (callers should default to 0)."""
    unique_ids = list(dict.fromkeys(user_ids))
    if not unique_ids:
        return {}
    stmt = (
        select(VM.owner_id, func.count(VM.id))
        .where(VM.owner_id.in_(unique_ids))
        .where(VM.state.in_(_ACTIVE_VM_STATES))
        .group_by(VM.owner_id)
    )
    rows = (await session.execute(stmt)).all()
    return {owner_id: int(count) for owner_id, count in rows}


async def check_batch_quota(session: AsyncSession, *, owner_ids: list[str]) -> None:
    """Same as ``check_user_vm_quota`` but for a batch with possibly
    multiple items per owner (each row in ``owner_ids`` is one requested VM).

    Counts duplicate owner_ids in the batch toward ``extra_requested`` so a
    deploy of 4 VMs for the same alice with limit 3 fails clean.

    Resolves all limits and active counts in a constant number of queries
    (two for limits, one grouped count) instead of three per owner, then
    evaluates each owner in deterministic order so the first one over the
    limit is reported — matching the old per-owner loop's behaviour.
    """
    counts = Counter(owner_ids)
    unique_owners = list(counts.keys())
    limits = await resolve_vm_quotas_for_users(session, unique_owners)
    active = await count_active_vms_for_users(session, unique_owners)

    for owner in unique_owners:
        requested = counts[owner]
        limit = limits.get(owner, DEFAULT_VMS_PER_USER)
        current = active.get(owner, 0)
        if current + requested > limit:
            raise QuotaExceededError(
                owner_id=owner,
                current=current,
                limit=limit,
                extra_requested=requested,
            )
