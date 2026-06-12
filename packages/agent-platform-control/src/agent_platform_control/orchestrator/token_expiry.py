"""Bootstrap-token TTL sweeper (PR-D, decision 8).

Decision 8 (locked 2026-05-28): a bootstrap token issued at extraConfig
injection time has a 30-minute TTL. If cloud-init inside the VM never
redeems it within that window, the deployment item is marked failed and
the cleanup cron eventually destroys the orphan VM (decision 5, see
``cleanup_cron.py``).

This module is **independent of the DeploymentWorker**: it inspects DB
state and writes failed transitions but does not consume / share the
worker's claim/process loop. That keeps the TTL clock honest even when
the worker is down, and avoids any need to extend the worker's existing
state machine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import Deployment, DeploymentItem
from .deployment_state import recompute_deployment_state

logger = logging.getLogger(__name__)

# Decision 8: 30-minute default. Configurable via the public sweep signature
# so prod can shorten it for testing or lengthen it for slow customer networks.
DEFAULT_TTL_SECONDS = 30 * 60


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _coerce_aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on roundtrip; treat naive datetimes as UTC.

    Mirrors the helper in worker.py — duplicated here so this module has
    zero import-time dependency on the wait_cloud_init code path that
    PR-A introduces.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _expiry_message(item: DeploymentItem, ttl_seconds: int) -> str:
    return (
        f"bootstrap token issued at {item.token_issued_at!s} expired before "
        f"cloud-init inside the VM redeemed it (TTL = {ttl_seconds // 60} min). "
        f"Check the VM console for cloud-init errors "
        f"(/var/log/cloud-init-output.log) or use "
        f"POST /v1/deployments/from-approval/{{request_id}}?force=true to retry "
        f"with a fresh token. See decision 8."
    )


async def _sweep_expired_in_session(
    session: AsyncSession,
    *,
    ttl_seconds: int,
    now: datetime,
) -> int:
    """Single-pass scan; caller owns the session."""
    cutoff = now - timedelta(seconds=ttl_seconds)

    # Pick candidates whose clock is started + not yet consumed. State filter
    # excludes already-terminal items so re-sweeping is cheap.
    stmt = select(DeploymentItem).where(
        DeploymentItem.token_issued_at.is_not(None),
        DeploymentItem.token_consumed_at.is_(None),
        DeploymentItem.state.in_(("pending", "cloning", "powered_on")),
    )
    candidates = (await session.execute(stmt)).scalars().all()

    expired = [item for item in candidates if _coerce_aware(item.token_issued_at) < cutoff]

    if not expired:
        return 0

    # Group items by deployment so we can recompute aggregate counts once.
    deps_touched: dict[str, Deployment] = {}
    for item in expired:
        item.state = "failed"
        item.error_message = _expiry_message(item, ttl_seconds)
        item.updated_at = now

        dep_id = item.deployment_id
        if dep_id not in deps_touched:
            dep = await session.get(Deployment, dep_id)
            if dep is not None:
                deps_touched[dep_id] = dep
        if dep_id in deps_touched:
            deps_touched[dep_id].failed_count += 1

    for dep in deps_touched.values():
        # Aggregate state recompute delegates to the single source of truth
        # (deployment_state.py) — the hand-rolled copy here had drifted: it
        # ignored cancelled_count, leaving mixed cancelled+expired
        # deployments stuck in 'running' forever.
        recompute_deployment_state(dep)

    logger.info("bootstrap-token TTL sweep marked %s deployment_items failed", len(expired))
    return len(expired)


async def sweep_expired_tokens(
    sm: async_sessionmaker[AsyncSession],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> int:
    """One sweep pass over the DB. Returns the count of items just marked failed.

    Designed to be called from a cron coroutine (``cleanup_cron.py``)
    every minute or so. Idempotent — re-running on an already-failed
    item is a no-op because the state filter excludes terminal states.
    """
    async with sm() as session, session.begin():
        return await _sweep_expired_in_session(session, ttl_seconds=ttl_seconds, now=_utcnow())
