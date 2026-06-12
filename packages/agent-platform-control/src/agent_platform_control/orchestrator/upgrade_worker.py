"""UpgradeWorker — drives the blue-green state machine forward.

Pattern mirrors ``DeploymentWorker`` (PR #81 / commit 57a4ffa):
  - One asyncio coroutine; pulls one Upgrade per loop in a "live" state.
  - vCenter side effects are STUBBED — every real op becomes
    ``await asyncio.sleep(0.1)`` + an INFO log line tagged ``TODO(task-1.3)``.
    Real wiring lands in #103 (vcsim provisioner) and a follow-up MCP-tool
    based path.
  - On programmer-bug exceptions the upgrade is moved to ``failed`` with the
    exception message stashed in ``failure_reason``. No silent retries.

States the worker advances automatically (see ``upgrade_state._AUTO_EDGES``):

    planned                  -> provisioning_blue
    provisioning_blue        -> home_volume_attaching
    home_volume_attaching    -> blue_ready
    cutover_in_progress      -> cutover_done
    cutover_done             -> cleanup_pending     (only after API /cleanup)
    cleanup_pending          -> completed

The worker does NOT auto-cutover from ``blue_ready`` or auto-cleanup from
``cutover_done`` — those edges are user-triggered via the API for safety.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import Upgrade
from . import upgrade_state as us

logger = logging.getLogger("agent_platform_control.orchestrator.upgrade")


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Subset of states the worker advances on its own each tick.
_DRIVABLE_STATES: frozenset[str] = frozenset(
    {
        us.PLANNED,
        us.PROVISIONING_BLUE,
        us.HOME_VOLUME_ATTACHING,
        us.CUTOVER_IN_PROGRESS,
        us.CLEANUP_PENDING,
    }
)


class UpgradeWorker:
    """Drains in-flight Upgrade rows. Start with ``.start()``, stop with ``.stop()``."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        poll_interval_s: float = 1.0,
        step_delay_s: float = 0.1,
    ) -> None:
        self._sm = sessionmaker
        self._poll_interval_s = poll_interval_s
        # Pulled out so tests can drop it to 0 — represents vCenter stub latency.
        self._step_delay_s = step_delay_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ---- lifecycle ----

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="upgrade-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def drain_once(self) -> int:
        """Advance one upgrade by one state, if any is drivable. Public for tests."""
        upgrade_id = await self._claim_one()
        if upgrade_id is None:
            return 0
        await self._process(upgrade_id)
        return 1

    # ---- internals ----

    async def _run(self) -> None:
        logger.info("upgrade-worker started")
        while not self._stop_event.is_set():
            try:
                processed = await self.drain_once()
            except Exception:
                logger.exception("upgrade-worker loop error")
                processed = 0
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), self._poll_interval_s)
        logger.info("upgrade-worker stopped")

    async def _claim_one(self) -> str | None:
        """Pick one Upgrade in a drivable state. We don't lock the row — for the
        skeleton, a single worker is assumed. Multi-worker safety arrives with
        Task 1.3 (real provisioner).
        """
        async with self._sm() as s:
            row = (
                await s.execute(
                    select(Upgrade.id)
                    .where(Upgrade.state.in_(_DRIVABLE_STATES))
                    .order_by(Upgrade.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row

    async def _process(self, upgrade_id: str) -> None:
        async with self._sm() as s:
            up = await s.get(Upgrade, upgrade_id)
            if up is None:
                return
            await s.refresh(up, attribute_names=["vms"])
            current = us.UpgradeState(value=up.state)

            try:
                # Dispatch by state. Each handler is a vCenter-stub
                # placeholder — real work lands in task 1.3 / PR #103.
                if current.value == us.PLANNED:
                    await self._do_plan(up)
                elif current.value == us.PROVISIONING_BLUE:
                    await self._do_provision_blue(up)
                elif current.value == us.HOME_VOLUME_ATTACHING:
                    await self._do_attach_home(up)
                elif current.value == us.CUTOVER_IN_PROGRESS:
                    await self._do_cutover(up)
                elif current.value == us.CLEANUP_PENDING:
                    await self._do_cleanup(up)
                else:
                    # Caller bug: _claim_one only returns drivable states.
                    logger.warning(
                        "upgrade %s in non-drivable state %s; skipping",
                        upgrade_id,
                        current.value,
                    )
                    return
                new_state = current.advance()
            except Exception as exc:
                logger.exception("upgrade %s step failed", upgrade_id)
                new_state = current.fail()
                up.failure_reason = f"{type(exc).__name__}: {exc}"

            await s.execute(
                update(Upgrade)
                .where(Upgrade.id == upgrade_id)
                .values(
                    state=new_state.value, updated_at=_utcnow(), failure_reason=up.failure_reason
                )
            )
            await s.commit()
            logger.info("upgrade %s advanced: %s -> %s", upgrade_id, current.value, new_state.value)

    # ---- per-state stubs ----
    #
    # TODO(task-1.3, PR #103): replace asyncio.sleep with real calls into the
    #   vmware-aiops provisioner. Wrap every write with vmware-policy's
    #   ``@vmware_tool`` decorator once that path is wired into C1 (CLAUDE.md
    #   "破坏性操作必须走 @vmware_tool 记审计").

    async def _do_plan(self, up: Upgrade) -> None:
        logger.info(
            "TODO(task-1.3): validate plan for upgrade=%s (tenant=%s, %s -> %s)",
            up.id,
            up.tenant_id,
            up.from_version,
            up.to_version,
        )
        await asyncio.sleep(self._step_delay_s)

    async def _do_provision_blue(self, up: Upgrade) -> None:
        for vm in up.vms:
            if vm.role != "blue":
                continue
            logger.info(
                "TODO(task-1.3): clone blue VM name=%s for upgrade=%s",
                vm.intended_name,
                up.id,
            )
            vm.status = "provisioned"
            vm.vm_id = f"stub-blue-{vm.id}"
            vm.updated_at = _utcnow()
            await asyncio.sleep(self._step_delay_s)

    async def _do_attach_home(self, up: Upgrade) -> None:
        # Home-volume copy depends on C19 (vSAN File Services); the skeleton
        # just walks through the state. See R7 in design doc — copy, not move.
        logger.info(
            "TODO(task-1.12.2, blocked-on-C19): copy /home volumes for upgrade=%s",
            up.id,
        )
        await asyncio.sleep(self._step_delay_s)

    async def _do_cutover(self, up: Upgrade) -> None:
        logger.info(
            "TODO(task-1.3): flip routing to blue side for upgrade=%s",
            up.id,
        )
        await asyncio.sleep(self._step_delay_s)

    async def _do_cleanup(self, up: Upgrade) -> None:
        for vm in up.vms:
            if vm.role != "green":
                continue
            logger.info(
                "TODO(task-1.3): cold-store/delete green VM %s for upgrade=%s",
                vm.vm_id,
                up.id,
            )
            vm.status = "retired"
            vm.updated_at = _utcnow()
            await asyncio.sleep(self._step_delay_s)
