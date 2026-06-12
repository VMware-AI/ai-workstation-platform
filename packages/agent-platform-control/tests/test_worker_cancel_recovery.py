"""Regression tests for PR-review #81 / #130 deferred follow-ups.

Covers four behaviours that were broken or missing on main:
  * cancel terminality — a deployment with cancelled items reaches a terminal
    state instead of being stuck in 'running' (worker terminality ignored
    cancelled items) [#81]
  * resurrection guard — an in-flight item that fails after its deployment was
    cancelled is NOT requeued [#81]
  * crash recovery — items stranded in 'cloning' are reset to 'pending' on
    startup [#81]
  * cloud-init sweep atomicity — an already-advanced item is not double-counted
    when the sweep runs again [#130]
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import Deployment, DeploymentItem, Tenant, User
from agent_platform_control.orchestrator import DeploymentWorker
from agent_platform_control.orchestrator.deployment_state import recompute_deployment_state
from agent_platform_control.orchestrator.fake import FakeProvisioner
from agent_platform_control.orchestrator.tokens import encrypt_user_token, hash_user_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

WORKER_ARGS = {
    "registry_url": "registry.test/agent-platform",
    "goose_image_tag": "1.34.1",
    "litellm_gateway_url": "http://gw.test:4000",
    "heartbeat_url": "http://ctl.test/v1/heartbeat",
    "poll_interval_s": 0.05,
}


def _token_fields(token: str) -> dict[str, str]:
    return {"user_token_enc": encrypt_user_token(token), "user_token_hash": hash_user_token(token)}


@pytest_asyncio.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(sm, *, items: list[tuple[str, str]], requested: int) -> None:
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        for owner in {o for o, _ in items}:
            s.add(User(id=owner, email=f"{owner}@x", display_name=owner, tenant_id="t-a"))
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="[t] agent-platform.vmtx",
                image_version="v0.1.0",
                state="running",
                requested_count=requested,
            )
        )
        for owner, state in items:
            s.add(
                DeploymentItem(
                    deployment_id="dep-1",
                    owner_id=owner,
                    intended_name=f"vm-{owner}",
                    state=state,
                    **_token_fields(f"tok_{owner}"),
                )
            )
        await s.commit()


def test_recompute_counts_cancelled_toward_terminality():
    """succeeded + failed + cancelled >= requested → terminal (not stuck running)."""
    dep = Deployment(
        id="d",
        tenant_id="t",
        requested_by="a",
        template="t",
        image_version="v",
        state="running",
        requested_count=2,
        succeeded_count=1,
        failed_count=0,
        cancelled_count=1,
    )
    recompute_deployment_state(dep)
    assert dep.state == "partially_failed"  # 1 made, 1 cancelled → not 'running'

    dep2 = Deployment(
        id="d2",
        tenant_id="t",
        requested_by="a",
        template="t",
        image_version="v",
        state="running",
        requested_count=2,
        succeeded_count=0,
        failed_count=0,
        cancelled_count=2,
    )
    recompute_deployment_state(dep2)
    assert dep2.state == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_inflight_item_not_requeued(sm):
    """A clone that fails after the deployment was cancelled is retired, not retried."""
    await _seed(sm, items=[("alice", "cloning")], requested=1)
    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        dep.state = "cancelled"  # admin cancelled while alice was cloning
        await s.commit()

    # FakeProvisioner that always fails — a non-cancelled item would requeue.
    class _FailProvisioner(FakeProvisioner):
        async def clone_vm(self, spec):  # type: ignore[override]
            from agent_platform_control.orchestrator.protocol import CloneResult

            return CloneResult(success=False, error="boom")

    worker = DeploymentWorker(sm, _FailProvisioner(), **WORKER_ARGS)
    await worker._process_item(await _first_item_id(sm))

    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalar_one()
        assert item.state == "cancelled", "must not requeue or fail a cancelled item"
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "cancelled"
        assert dep.cancelled_count == 1


@pytest.mark.asyncio
async def test_cancel_during_clone_is_not_reverted_or_resurrected(sm):
    """Cancel that commits WHILE the worker is awaiting the clone must stick.

    Regression for the review's C1: _apply_result read a `dep` snapshot taken
    before the awaited clone, so a mid-clone cancel was reverted (state back to
    running) and the failed item requeued. The provisioner here commits the
    cancel from inside clone_vm to reproduce that exact ordering.
    """
    await _seed(sm, items=[("alice", "pending"), ("bob", "pending")], requested=2)

    class _CancelMidClone(FakeProvisioner):
        async def clone_vm(self, spec):  # type: ignore[override]
            from agent_platform_control.orchestrator.protocol import CloneResult

            # Simulate POST /{id}/cancel committing while we're "cloning":
            # cancel the still-pending sibling and mark the deployment cancelled.
            async with sm() as s2:
                dep = await s2.get(Deployment, "dep-1")
                others = (
                    (
                        await s2.execute(
                            select(DeploymentItem).where(DeploymentItem.state == "pending")
                        )
                    )
                    .scalars()
                    .all()
                )
                for it in others:
                    it.state = "cancelled"
                dep.cancelled_count += len(others)
                dep.state = "cancelled"
                await s2.commit()
            return CloneResult(success=False, error="boom mid-cancel")

    async with sm() as s:
        alice_id = (
            await s.execute(select(DeploymentItem.id).where(DeploymentItem.owner_id == "alice"))
        ).scalar_one()

    worker = DeploymentWorker(sm, _CancelMidClone(), **WORKER_ARGS)
    await worker._process_item(alice_id)

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "cancelled", "mid-clone cancel must not be reverted to running"
        items = {
            i.owner_id: i.state for i in (await s.execute(select(DeploymentItem))).scalars().all()
        }
        # the clamed item failed under a cancelled deployment → retired, NOT requeued
        assert items["alice"] == "cancelled"
        assert items["bob"] == "cancelled"


@pytest.mark.asyncio
async def test_recover_stale_cloning_resets_to_pending(sm):
    """An item stuck in 'cloning' past the lease is reset to 'pending'."""
    await _seed(sm, items=[("alice", "cloning")], requested=1)
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalar_one()
        item.updated_at = datetime.now(UTC) - timedelta(hours=1)  # stale
        await s.commit()

    worker = DeploymentWorker(sm, FakeProvisioner(), stale_cloning_lease_s=900.0, **WORKER_ARGS)
    recovered = await worker.recover_stale_cloning()

    assert recovered == 1
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalar_one()
        assert item.state == "pending"


@pytest.mark.asyncio
async def test_recover_leaves_fresh_cloning_untouched(sm):
    """A recently-updated 'cloning' item (within lease) is not reset."""
    await _seed(sm, items=[("alice", "cloning")], requested=1)
    worker = DeploymentWorker(sm, FakeProvisioner(), stale_cloning_lease_s=900.0, **WORKER_ARGS)
    recovered = await worker.recover_stale_cloning()
    assert recovered == 0
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalar_one()
        assert item.state == "cloning"


@pytest.mark.asyncio
async def test_sweep_does_not_double_count_already_ready(sm):
    """Re-running the cloud-init sweep over a ready item does not re-increment counts."""
    await _seed(sm, items=[("alice", "powered_on")], requested=1)
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalar_one()
        item.token_consumed_at = datetime.now(UTC)  # cloud-init redeemed
        await s.commit()

    worker = DeploymentWorker(sm, FakeProvisioner(), wait_cloud_init=True, **WORKER_ARGS)
    first = await worker._advance_cloud_init()
    second = await worker._advance_cloud_init()  # idempotent — item already 'ready'

    assert first == 1
    assert second == 0
    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.succeeded_count == 1  # NOT 2
        assert dep.state == "completed"


async def _first_item_id(sm) -> int:
    async with sm() as s:
        return (await s.execute(select(DeploymentItem.id))).scalar_one()
