"""Tests for orchestrator.cleanup_cron — decision-5 failed VM destruction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import Deployment, DeploymentItem, Tenant, User
from agent_platform_control.orchestrator.cleanup_cron import (
    FailedVmCleanupCron,
    cleanup_failed_vms,
)
from agent_platform_control.orchestrator.fake import FakeProvisioner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture
async def seeded(engine):
    """4 items: 2 fresh-failed + 1 stale-failed + 1 stale-failed-no-vm."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    long_ago = now - timedelta(hours=48)
    just_now = now - timedelta(hours=1)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="tpl",
                image_version="v0.1.0",
                state="partially_failed",
                requested_count=4,
                failed_count=3,
            )
        )
        # 1: stale-failed with VM → eligible for cleanup
        s.add(
            DeploymentItem(
                id=1,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-a",
                user_token_enc="e1",
                user_token_hash="h1",
                vm_id="vsphere-vm-001",
                state="failed",
                updated_at=long_ago,
            )
        )
        # 2: fresh-failed (within retain window) → not yet eligible
        s.add(
            DeploymentItem(
                id=2,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-b",
                user_token_enc="e2",
                user_token_hash="h2",
                vm_id="vsphere-vm-002",
                state="failed",
                updated_at=just_now,
            )
        )
        # 3: stale-failed but no VM bound → not eligible (nothing to destroy)
        s.add(
            DeploymentItem(
                id=3,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-c",
                user_token_enc="e3",
                user_token_hash="h3",
                vm_id=None,
                state="failed",
                updated_at=long_ago,
            )
        )
        # 4: stale-failed, second eligible
        s.add(
            DeploymentItem(
                id=4,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-d",
                user_token_enc="e4",
                user_token_hash="h4",
                vm_id="vsphere-vm-004",
                state="failed",
                updated_at=long_ago,
            )
        )
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_cleanup_destroys_stale_failed_vms_with_vm_id(seeded):
    fake = FakeProvisioner()
    n = await cleanup_failed_vms(seeded, fake, retain_hours=24)
    assert n == 2
    assert sorted(fake.destroyed) == ["vsphere-vm-001", "vsphere-vm-004"]


@pytest.mark.asyncio
async def test_cleanup_skips_fresh_failed(seeded):
    fake = FakeProvisioner()
    await cleanup_failed_vms(seeded, fake, retain_hours=24)
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[2].state == "failed"
    assert "vsphere-vm-002" not in fake.destroyed


@pytest.mark.asyncio
async def test_cleanup_skips_failed_without_vm_id(seeded):
    fake = FakeProvisioner()
    await cleanup_failed_vms(seeded, fake, retain_hours=24)
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    # Stale but unbound → stays in failed (nothing to destroy)
    assert items[3].state == "failed"


@pytest.mark.asyncio
async def test_cleanup_marks_state_cleaned(seeded):
    fake = FakeProvisioner()
    await cleanup_failed_vms(seeded, fake, retain_hours=24)
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[1].state == "cleaned"
    assert items[4].state == "cleaned"


@pytest.mark.asyncio
async def test_cleanup_idempotent(seeded):
    """Re-running after a successful sweep is a no-op (state filter excludes cleaned)."""
    fake = FakeProvisioner()
    first = await cleanup_failed_vms(seeded, fake, retain_hours=24)
    second = await cleanup_failed_vms(seeded, fake, retain_hours=24)
    assert first == 2
    assert second == 0
    # destroy_vm not called again
    assert len(fake.destroyed) == 2


@pytest.mark.asyncio
async def test_cleanup_continues_when_one_destroy_fails(seeded):
    """One bad VM doesn't block the rest — log + skip + retry next sweep."""

    class FlakyProvisioner(FakeProvisioner):
        async def destroy_vm(self, vm_id):
            if vm_id == "vsphere-vm-001":
                raise RuntimeError("vCenter blip")
            await super().destroy_vm(vm_id)

    fake = FlakyProvisioner()
    n = await cleanup_failed_vms(seeded, fake, retain_hours=24)
    # Only vm-004 cleaned this pass
    assert n == 1
    assert fake.destroyed == ["vsphere-vm-004"]
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[1].state == "failed"  # still stale, will retry
    assert items[4].state == "cleaned"


@pytest.mark.asyncio
async def test_cleanup_commits_each_item_independently(seeded):
    """AC1 (#353): each destroy + mark-cleaned commits in its own transaction,
    so a slow vCenter destroy never holds a write lock across the whole sweep.
    Observed from a separate session during each destroy, the committed-cleaned
    count grows mid-sweep — a single batch txn would show 0 until the very end.
    """
    seen_cleaned: list[int] = []

    class ObservingProvisioner(FakeProvisioner):
        async def destroy_vm(self, vm_id):
            async with seeded() as s2:
                rows = (await s2.execute(select(DeploymentItem))).scalars().all()
                seen_cleaned.append(sum(1 for i in rows if i.state == "cleaned"))
            await super().destroy_vm(vm_id)

    fake = ObservingProvisioner()
    total = await cleanup_failed_vms(seeded, fake, retain_hours=24)
    assert total == 2
    # First destroy: nothing committed-cleaned yet (0). By the last destroy the
    # earlier item has already committed (>=1) — proof of per-item transactions.
    assert seen_cleaned[0] == 0
    assert seen_cleaned[-1] >= 1


@pytest.mark.asyncio
async def test_cron_class_sweep_once_works(seeded):
    """Smoke test the cron wrapper without firing up the background loop."""
    fake = FakeProvisioner()
    cron = FailedVmCleanupCron(seeded, fake, retain_hours=24)
    n = await cron.sweep_once()
    assert n == 2
