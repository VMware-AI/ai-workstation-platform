"""Unit tests for the PeriodicTask base (#352 part 3)."""

from __future__ import annotations

import asyncio

import pytest
from agent_platform_control.orchestrator.periodic_task import PeriodicTask


class _CountingTask(PeriodicTask):
    _task_name = "counting-task"

    def __init__(self, *, did_work: bool, poll_interval_s: float = 0.01) -> None:
        self._poll_interval_s = poll_interval_s
        self.ticks = 0
        self.started = 0
        self._did_work = did_work
        self._init_periodic()

    async def _on_start(self) -> None:
        self.started += 1

    async def _tick(self) -> bool:
        self.ticks += 1
        return self._did_work


@pytest.mark.asyncio
async def test_runs_ticks_then_stops_promptly():
    t = _CountingTask(did_work=True)
    t.start()
    await asyncio.sleep(0.03)
    await t.stop()
    assert t.started == 1
    assert t.ticks >= 2  # did_work=True → loops immediately, no idle wait
    assert t._task is None


@pytest.mark.asyncio
async def test_idle_backoff_waits_poll_interval():
    # did_work=False → waits one poll interval between ticks, so far fewer ticks.
    t = _CountingTask(did_work=False, poll_interval_s=0.05)
    t.start()
    await asyncio.sleep(0.02)  # less than one poll interval
    await t.stop()
    # One tick fired, then it parked on the stop_event for the poll interval
    # (interrupted by stop) — so the loop did not spin.
    assert t.ticks == 1


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_is_safe_when_not_started():
    t = _CountingTask(did_work=False)
    await t.stop()  # no-op when never started
    assert t._task is None
    t.start()
    first = t._task
    t.start()  # second start is a no-op, same task
    assert t._task is first
    await t.stop()


@pytest.mark.asyncio
async def test_tick_exception_does_not_kill_the_loop():
    class _Boom(PeriodicTask):
        _task_name = "boom"

        def __init__(self) -> None:
            self._poll_interval_s = 0.01
            self.ticks = 0
            self._init_periodic()

        async def _tick(self) -> bool:
            self.ticks += 1
            raise RuntimeError("boom")

    t = _Boom()
    t.start()
    await asyncio.sleep(0.03)
    await t.stop()
    # The loop survived the exceptions and kept ticking (backoff after each).
    assert t.ticks >= 2
