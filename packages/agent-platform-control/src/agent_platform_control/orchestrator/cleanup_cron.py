"""Failed-VM cleanup cron (PR-D, decision 5).

Decision 5 (locked 2026-05-28): a deployment item that lands in ``failed``
sticks around for ``failed_vm_retain_hours`` (default 24) so admins can
inspect cloud-init logs / vCenter console before the cron destroys the
orphan VM. Once the retain window elapses, the cron:

  1. calls ``Provisioner.destroy_vm`` on the bound vCenter VM
  2. flips ``DeploymentItem.state`` from ``failed`` → ``cleaned``
  3. revokes any per-VM secrets via ``VmSecretStore`` if one is wired
     (decision 6 cleanup follow-up — wired through C-4's helper if
     available, no-op otherwise)

The cron is **disabled by default** (``cleanup_cron_enabled=False``) so
the M1 demo path doesn't accidentally chew through a tenant's quota
before customer ops has signed off on the recovery runbook.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import VM, DeploymentItem
from .audit import record_audit, sanitize
from .protocol import Provisioner

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _coerce_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def _cleanup_in_session(
    session: AsyncSession,
    provisioner: Provisioner,
    *,
    retain_hours: int,
    now: datetime,
) -> int:
    """Single pass: destroy retained-failed items, mark cleaned. Caller owns commit."""
    cutoff = now - timedelta(hours=retain_hours)

    stmt = select(DeploymentItem).where(
        DeploymentItem.state == "failed",
        DeploymentItem.vm_id.is_not(None),
    )
    rows = (await session.execute(stmt)).scalars().all()
    eligible = [item for item in rows if _coerce_aware(item.updated_at) < cutoff]

    cleaned = 0
    for item in eligible:
        try:
            await provisioner.destroy_vm(item.vm_id)
        except Exception as exc:
            # Don't let one bad VM block the rest. Log + audit the failure +
            # skip; next sweep picks it up again.
            logger.exception(
                "destroy_vm failed for deployment_item %s vm_id=%s; will retry next sweep",
                item.id,
                item.vm_id,
            )
            record_audit(
                session,
                actor="cron:cleanup",
                operation="vm.destroy",
                resource=item.vm_id,
                params={
                    "deployment_item_id": item.id,
                    "reason": "failed-vm-retention",
                    "error": sanitize(str(exc)),
                },
                result="failure",
            )
            continue

        item.state = "cleaned"
        item.updated_at = now
        cleaned += 1
        # Auto-destroy must be traceable: who/what/when/why (harness #H-7).
        record_audit(
            session,
            actor="cron:cleanup",
            operation="vm.destroy",
            resource=item.vm_id,
            params={"deployment_item_id": item.id, "reason": "failed-vm-retention"},
            result="success",
        )

    if cleaned:
        logger.info("cleanup cron destroyed %s failed VMs older than %sh", cleaned, retain_hours)
    return cleaned


async def cleanup_failed_vms(
    sm: async_sessionmaker[AsyncSession],
    provisioner: Provisioner,
    *,
    retain_hours: int = 24,
) -> int:
    """One sweep over the DB. Returns the count of items just cleaned."""
    async with sm() as session, session.begin():
        return await _cleanup_in_session(
            session, provisioner, retain_hours=retain_hours, now=_utcnow()
        )


class FailedVmCleanupCron:
    """Background coroutine that sweeps every ``poll_interval_s`` seconds.

    Mirrors the DeploymentWorker pattern (start/stop, stop_event). Single
    instance per deployment; in M2 we may serialise with a DB advisory lock
    if multiple control-plane replicas need to run cleanup concurrently.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        provisioner: Provisioner,
        *,
        retain_hours: int = 24,
        poll_interval_s: float = 3600.0,
    ) -> None:
        self._sm = sessionmaker
        self._provisioner = provisioner
        self._retain_hours = retain_hours
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="failed-vm-cleanup-cron")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def sweep_once(self) -> int:
        """Public for tests — runs one cleanup pass and returns the count."""
        return await cleanup_failed_vms(
            self._sm, self._provisioner, retain_hours=self._retain_hours
        )

    async def _run(self) -> None:
        logger.info("failed-vm-cleanup-cron started")
        while not self._stop_event.is_set():
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("cleanup cron sweep crashed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), self._poll_interval_s)
        logger.info("failed-vm-cleanup-cron stopped")


# ============================================================ heartbeat sweeper
# Decision 16: 5min no heartbeat → unhealthy; 30min → lost.


UNHEALTHY_AFTER_SECONDS = 5 * 60
LOST_AFTER_SECONDS = 30 * 60


async def _heartbeat_sweep_in_session(
    session: AsyncSession,
    *,
    now: datetime,
    unhealthy_after_s: int,
    lost_after_s: int,
) -> tuple[int, int]:
    """Single pass. Returns (unhealthy_count, lost_count)."""
    unhealthy_cutoff = now - timedelta(seconds=unhealthy_after_s)
    lost_cutoff = now - timedelta(seconds=lost_after_s)

    # Only consider VMs that have heartbeated at least once. Pre-heartbeat
    # VMs (boot in flight) are the worker's responsibility, not ours.
    stmt = select(VM).where(
        VM.last_heartbeat_at.is_not(None),
        VM.state.in_(("running", "unhealthy")),
    )
    rows = (await session.execute(stmt)).scalars().all()

    unhealthy = 0
    lost = 0
    for vm in rows:
        last = _coerce_aware(vm.last_heartbeat_at)
        if last < lost_cutoff and vm.state != "lost":
            vm.state = "lost"
            lost += 1
        elif lost_cutoff <= last < unhealthy_cutoff and vm.state != "unhealthy":
            vm.state = "unhealthy"
            unhealthy += 1

    if unhealthy or lost:
        logger.info("heartbeat sweep: %s marked unhealthy, %s marked lost", unhealthy, lost)
    return unhealthy, lost


async def heartbeat_sweep(
    sm: async_sessionmaker[AsyncSession],
    *,
    unhealthy_after_s: int = UNHEALTHY_AFTER_SECONDS,
    lost_after_s: int = LOST_AFTER_SECONDS,
) -> tuple[int, int]:
    """One sweep over the DB. Returns (unhealthy_count, lost_count)."""
    async with sm() as session, session.begin():
        return await _heartbeat_sweep_in_session(
            session,
            now=_utcnow(),
            unhealthy_after_s=unhealthy_after_s,
            lost_after_s=lost_after_s,
        )


class HeartbeatSweeper:
    """Background coroutine that classifies VM liveness every ``poll_interval_s``."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        unhealthy_after_s: int = UNHEALTHY_AFTER_SECONDS,
        lost_after_s: int = LOST_AFTER_SECONDS,
        poll_interval_s: float = 60.0,
    ) -> None:
        self._sm = sessionmaker
        self._unhealthy_after_s = unhealthy_after_s
        self._lost_after_s = lost_after_s
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="heartbeat-sweeper")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def sweep_once(self) -> tuple[int, int]:
        return await heartbeat_sweep(
            self._sm,
            unhealthy_after_s=self._unhealthy_after_s,
            lost_after_s=self._lost_after_s,
        )

    async def _run(self) -> None:
        logger.info("heartbeat-sweeper started")
        while not self._stop_event.is_set():
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("heartbeat sweep crashed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), self._poll_interval_s)
        logger.info("heartbeat-sweeper stopped")
