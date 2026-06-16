"""Tests for orchestrator.approval_redeploy — decision 7 force=true core.

The ``?force=true`` endpoint wiring lands in a small follow-up after PR
#129's from-approval handler merges to main. These tests cover the helper
the wiring will call.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_control.db.models import Deployment, DeploymentItem, Tenant, User
from agent_platform_control.orchestrator.approval_redeploy import (
    find_deployment_for_approval,
    revoke_deployment_for_approval,
)
from agent_platform_control.orchestrator.fake import FakeProvisioner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


# Minimal stand-in for the PR-C VmSecretStore — inline so this test file
# does not depend on PR-C landing on main. After PR-C merges, the helper
# happily accepts the real InMemoryVmSecretStore (same async signature).
class _StubStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str, str]] = []
        self.data: dict[tuple[str, str, str], str] = {}

    async def get(self, ref):
        return self.data.get((ref.layer, ref.scope_id, ref.key))

    async def put(self, ref, value):
        self.data[(ref.layer, ref.scope_id, ref.key)] = value

    async def delete(self, ref):
        self.data.pop((ref.layer, ref.scope_id, ref.key), None)
        self.deleted.append((ref.layer, ref.scope_id, ref.key))

    async def list_vm_refs(self, *, deployment_item_id: int):
        # Inline VmSecretRef-shaped dataclass so the helper's iteration works
        # whether or not PR-C is on this branch.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _R:
            layer: str
            scope_id: str
            key: str

        target = str(deployment_item_id)
        return [
            _R(layer, scope, key)
            for (layer, scope, key) in self.data
            if layer == "vm" and scope == target
        ]


@pytest_asyncio.fixture
async def seeded(engine):
    """Two deployments: one linked to approval id=7, one not."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        # Linked deployment
        s.add(
            Deployment(
                id="dep-linked",
                tenant_id="t-a",
                requested_by="alice",
                template="tpl",
                image_version="v0.1.0",
                state="running",
                requested_count=2,
                extra={"approval_request_id": 7, "source": "approval"},
            )
        )
        s.add(
            DeploymentItem(
                id=11,
                deployment_id="dep-linked",
                owner_id="alice",
                intended_name="vm-1",
                vm_id="vsphere-vm-001",
                user_token_enc="e1",
                user_token_hash="h1",
                state="powered_on",
            )
        )
        s.add(
            DeploymentItem(
                id=12,
                deployment_id="dep-linked",
                owner_id="alice",
                intended_name="vm-2",
                vm_id=None,  # never bound
                user_token_enc="e2",
                user_token_hash="h2",
                state="failed",
            )
        )
        # Unlinked deployment (no approval_request_id in extra)
        s.add(
            Deployment(
                id="dep-other",
                tenant_id="t-a",
                requested_by="alice",
                template="tpl",
                image_version="v0.1.0",
                state="completed",
                requested_count=1,
                extra={"source": "manual"},
            )
        )
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_find_returns_none_when_no_match(seeded):
    sm = seeded
    async with sm() as s:
        result = await find_deployment_for_approval(s, approval_request_id=99)
    assert result is None


@pytest.mark.asyncio
async def test_find_returns_linked_deployment(seeded):
    sm = seeded
    async with sm() as s:
        result = await find_deployment_for_approval(s, approval_request_id=7)
    assert result is not None
    assert result.id == "dep-linked"


@pytest.mark.asyncio
async def test_revoke_returns_false_when_no_match(seeded):
    sm = seeded
    fake = FakeProvisioner()
    revoked = await revoke_deployment_for_approval(sm, fake, approval_request_id=99)
    assert revoked is False
    assert fake.destroyed == []


@pytest.mark.asyncio
async def test_revoke_destroys_bound_vms_only(seeded):
    sm = seeded
    fake = FakeProvisioner()
    revoked = await revoke_deployment_for_approval(sm, fake, approval_request_id=7)
    assert revoked is True
    # Only item with vm_id is destroyed; the unbound one is skipped
    assert fake.destroyed == ["vsphere-vm-001"]


@pytest.mark.asyncio
async def test_revoke_records_audit_for_destroy(seeded):
    """Force-redeploy destroy must leave an audit row (harness #H-6)."""
    from agent_platform_control.db.models import AuditView

    sm = seeded
    await revoke_deployment_for_approval(sm, FakeProvisioner(), approval_request_id=7)
    async with sm() as s:
        destroys = [
            r
            for r in (await s.execute(select(AuditView))).scalars().all()
            if r.operation == "vm.destroy"
        ]
    assert len(destroys) == 1
    assert destroys[0].resource == "vsphere-vm-001"
    assert destroys[0].actor == "approval:redeploy"
    assert destroys[0].result == "success"


@pytest.mark.asyncio
async def test_revoke_marks_deployment_and_items_cancelled(seeded):
    sm = seeded
    fake = FakeProvisioner()
    await revoke_deployment_for_approval(sm, fake, approval_request_id=7)
    async with sm() as s:
        dep = await s.get(Deployment, "dep-linked")
        items = (
            (
                await s.execute(
                    select(DeploymentItem).where(DeploymentItem.deployment_id == "dep-linked")
                )
            )
            .scalars()
            .all()
        )
    assert dep.state == "cancelled"
    assert all(i.state == "cancelled" for i in items)


@pytest.mark.asyncio
async def test_revoke_doesnt_touch_unlinked_deployment(seeded):
    sm = seeded
    fake = FakeProvisioner()
    await revoke_deployment_for_approval(sm, fake, approval_request_id=7)
    async with sm() as s:
        other = await s.get(Deployment, "dep-other")
    assert other.state == "completed"


@pytest.mark.asyncio
async def test_revoke_with_stub_store_does_not_crash(seeded):
    """Helper accepts a store. Actual secret revocation needs PR-C's
    secret_provisioner; on this branch the late import silently fails and
    the helper continues — we just verify it doesn't blow up."""
    sm = seeded
    fake = FakeProvisioner()
    store = _StubStore()

    revoked = await revoke_deployment_for_approval(
        sm, fake, approval_request_id=7, secret_store=store
    )
    assert revoked is True


@pytest.mark.asyncio
async def test_revoke_commits_cancel_before_destroy(seeded):
    """AC1 (#353): the deployment is marked cancelled and committed BEFORE any
    destroy IO runs — so the destroy never executes inside an open write txn.
    Read from a *separate* session mid-destroy, the deployment must already be
    'cancelled'.
    """
    sm = seeded
    observed: list[str] = []

    class ObservingProvisioner(FakeProvisioner):
        async def destroy_vm(self, vm_id):
            async with sm() as s2:
                dep = await s2.get(Deployment, "dep-linked")
                observed.append(dep.state)
            await super().destroy_vm(vm_id)

    revoked = await revoke_deployment_for_approval(
        sm, ObservingProvisioner(), approval_request_id=7
    )
    assert revoked is True
    assert observed == ["cancelled"], (
        "deployment not committed-cancelled before destroy — revoke still holds "
        "a txn open across the destroy IO (AC1 regression)"
    )


@pytest.mark.asyncio
async def test_revoke_continues_when_destroy_fails(seeded):
    """One bad destroy doesn't block the rest — deployment still cancelled."""

    class FlakyProvisioner(FakeProvisioner):
        async def destroy_vm(self, vm_id):
            raise RuntimeError("vCenter blip")

    sm = seeded
    fake = FlakyProvisioner()
    revoked = await revoke_deployment_for_approval(sm, fake, approval_request_id=7)
    assert revoked is True
    async with sm() as s:
        dep = await s.get(Deployment, "dep-linked")
    # Deployment still marked cancelled — cleanup cron picks up the orphan VM later
    assert dep.state == "cancelled"
