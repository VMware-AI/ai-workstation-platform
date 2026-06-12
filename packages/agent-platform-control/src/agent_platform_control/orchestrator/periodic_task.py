"""PeriodicTask — shared asyncio background-loop lifecycle.

The DeploymentWorker, UpgradeWorker, FailedVmCleanupCron and HeartbeatSweeper
all ran the same hand-copied ``start`` / ``stop`` / ``_run`` boilerplate
(create_task, cooperative stop_event wait, 5s join-then-cancel). This base
class owns that loop once; subclasses provide only:

  - ``_task_name`` / ``_poll_interval_s`` (class or instance attributes)
  - ``_tick()`` — one iteration; return ``True`` if it did work (loop again
    immediately) or ``False`` to wait one poll interval before retrying.
    Periodic crons that should fire on a fixed cadence return ``False``.
  - ``_on_start()`` — optional one-time startup hook (e.g. recover stale rows);
    default no-op.

Subclasses MUST call :meth:`_init_periodic` from their ``__init__`` (it sets up
the task handle + stop event), or set ``self._task = None`` /
``self._stop_event = asyncio.Event()`` themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

logger = logging.getLogger("agent_platform_control.orchestrator")


class PeriodicTask:
    """Start/stop a single asyncio loop that calls :meth:`_tick` repeatedly."""

    _task_name: str = "periodic-task"
    _poll_interval_s: float = 1.0

    _task: asyncio.Task[None] | None
    _stop_event: asyncio.Event

    def _init_periodic(self) -> None:
        """Initialise the task handle + stop event. Call from ``__init__``."""
        self._task = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=self._task_name)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def _on_start(self) -> None:
        """One-time startup hook. Default no-op; override to recover state."""

    async def _tick(self) -> bool:
        """Run one iteration. Return ``True`` if work was done (loop again at
        once), ``False`` to wait one poll interval first."""
        raise NotImplementedError

    async def _run(self) -> None:
        logger.info("%s started", self._task_name)
        await self._on_start()
        while not self._stop_event.is_set():
            try:
                did_work = await self._tick()
            except Exception:
                logger.exception("%s loop error", self._task_name)
                did_work = False
            if not did_work:
                # Cooperate with stop(): wake immediately when the event is set.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), self._poll_interval_s)
            else:
                # Yield to the event loop between busy iterations so a _tick that
                # returns True without ever awaiting can't starve the loop.
                await asyncio.sleep(0)
        logger.info("%s stopped", self._task_name)
