"""Task 1.11.3 — worker waits for in-VM cloud-init before declaring success.

With ``wait_cloud_init=True`` a clone success is no longer terminal: the item
parks in ``powered_on`` (VM up, cloud-init running) and only advances to
``ready`` once the in-VM bootstrap redeems its token (the C18 exchange endpoint
stamps ``token_consumed_at``). If the token is never redeemed within
``cloud_init_timeout_s`` the item fails with an educational hint.

Default (``wait_cloud_init=False``) behaviour is covered by
``test_orchestrator_worker.py`` and intentionally unchanged.
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import Deployment, DeploymentItem, Tenant, User
from agent_platform_control.orchestrator import DeploymentWorker
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


@pytest_asyncio.fixture
async def seeded_batch(sm):
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        for owner in ("alice", "bob", "carol"):
            s.add(User(id=owner, email=f"{owner}@x", display_name=owner, tenant_id="t-a"))
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="[t] agent-platform.vmtx",
                image_version="v0.1.0",
                state="pending",
                requested_count=3,
            )
        )
        for owner in ("alice", "bob", "carol"):
            s.add(
                DeploymentItem(
                    deployment_id="dep-1",
                    owner_id=owner,
                    intended_name=f"vm-{owner}",
                    **_token_fields(f"tok_{owner}"),
                )
            )
        await s.commit()
    return sm


async def _drain_to_idle(worker, limit: int = 20) -> None:
    for _ in range(limit):
        if await worker.drain_once() == 0:
            break


@pytest.mark.asyncio
async def test_clone_parks_at_powered_on_not_succeeded(seeded_batch):
    """With wait on, clone success holds at powered_on and does NOT complete."""
    worker = DeploymentWorker(seeded_batch, FakeProvisioner(), wait_cloud_init=True, **WORKER_ARGS)

    await _drain_to_idle(worker)

    async with seeded_batch() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "running"  # NOT completed — cloud-init pending
        assert dep.succeeded_count == 0
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert {i.state for i in items} == {"powered_on"}


@pytest.mark.asyncio
async def test_token_consumed_advances_to_ready(seeded_batch):
    worker = DeploymentWorker(seeded_batch, FakeProvisioner(), wait_cloud_init=True, **WORKER_ARGS)
    await _drain_to_idle(worker)

    # Simulate the in-VM cloud-init redeeming alice's token (what the C18
    # exchange endpoint does on first boot).
    async with seeded_batch() as s:
        from datetime import datetime

        alice = (
            await s.execute(select(DeploymentItem).where(DeploymentItem.owner_id == "alice"))
        ).scalar_one()
        alice.token_consumed_at = datetime.now(UTC)
        await s.commit()

    await _drain_to_idle(worker)

    async with seeded_batch() as s:
        items = {
            i.owner_id: i.state for i in (await s.execute(select(DeploymentItem))).scalars().all()
        }
        assert items["alice"] == "ready"
        assert items["bob"] == "powered_on"  # still waiting on its own cloud-init
        assert items["carol"] == "powered_on"
        dep = await s.get(Deployment, "dep-1")
        assert dep.succeeded_count == 1
        assert dep.state == "running"


@pytest.mark.asyncio
async def test_all_tokens_consumed_completes_deployment(seeded_batch):
    worker = DeploymentWorker(seeded_batch, FakeProvisioner(), wait_cloud_init=True, **WORKER_ARGS)
    await _drain_to_idle(worker)

    async with seeded_batch() as s:
        from datetime import datetime

        for item in (await s.execute(select(DeploymentItem))).scalars().all():
            item.token_consumed_at = datetime.now(UTC)
        await s.commit()

    await _drain_to_idle(worker)

    async with seeded_batch() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "completed"
        assert dep.succeeded_count == 3
        assert dep.failed_count == 0
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert {i.state for i in items} == {"ready"}


@pytest.mark.asyncio
async def test_cloud_init_timeout_marks_failed_with_hint(seeded_batch):
    worker = DeploymentWorker(
        seeded_batch,
        FakeProvisioner(),
        wait_cloud_init=True,
        cloud_init_timeout_s=600.0,
        **WORKER_ARGS,
    )
    await _drain_to_idle(worker)

    # Backdate powered_on to an hour ago so the timeout has elapsed without
    # any token ever being redeemed.
    async with seeded_batch() as s:
        from datetime import datetime

        for item in (await s.execute(select(DeploymentItem))).scalars().all():
            item.updated_at = datetime.now(UTC) - timedelta(hours=1)
        await s.commit()

    await _drain_to_idle(worker)

    async with seeded_batch() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "failed"
        assert dep.failed_count == 3
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert {i.state for i in items} == {"failed"}
        assert all("cloud-init" in (i.error_message or "") for i in items)


@pytest.mark.asyncio
async def test_still_waiting_within_timeout_is_idle(seeded_batch):
    """An un-redeemed item inside its timeout window keeps the worker idle."""
    worker = DeploymentWorker(
        seeded_batch,
        FakeProvisioner(),
        wait_cloud_init=True,
        cloud_init_timeout_s=600.0,
        **WORKER_ARGS,
    )
    await _drain_to_idle(worker)

    # No token redeemed, well within timeout → drain reports idle, no change.
    assert await worker.drain_once() == 0
    async with seeded_batch() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert {i.state for i in items} == {"powered_on"}
        dep = await s.get(Deployment, "dep-1")
        assert dep.succeeded_count == 0
        assert dep.failed_count == 0
