"""Per-user VM count quota tests (PR-E E-4 + E-5, decision 15)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_control.db.models import VM, Tenant, User
from agent_platform_control.orchestrator.quota import (
    DEFAULT_VMS_PER_USER,
    QuotaExceededError,
    check_batch_quota,
    check_user_vm_quota,
    count_active_vms_for_user,
    resolve_vm_quota_for_user,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------- helpers


@pytest_asyncio.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_basic(
    sm,
    *,
    user_quota: int | None = None,
    tenant_quota: int | None = None,
) -> None:
    """One tenant, one user (alice), optional quotas."""
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A", quota_vms_per_user=tenant_quota))
        s.add(
            User(
                id="alice",
                email="a@x",
                display_name="A",
                tenant_id="t-a",
                quota_vms=user_quota,
            )
        )
        await s.commit()


async def _add_active_vms(sm, owner: str, n: int, state: str = "running") -> None:
    async with sm() as s:
        for i in range(n):
            s.add(
                VM(
                    id=f"vm-{owner}-{state}-{i}",
                    name=f"{owner}-{state}-{i}",
                    tenant_id="t-a",
                    owner_id=owner,
                    image_version="v0.1.0",
                    state=state,
                )
            )
        await s.commit()


# ---------------------------------------------------------- resolve fallback


@pytest.mark.asyncio
async def test_default_when_neither_user_nor_tenant_overrides(sm):
    await _seed_basic(sm)
    async with sm() as s:
        limit = await resolve_vm_quota_for_user(s, "alice")
    assert limit == DEFAULT_VMS_PER_USER


@pytest.mark.asyncio
async def test_tenant_override_takes_effect(sm):
    await _seed_basic(sm, tenant_quota=10)
    async with sm() as s:
        limit = await resolve_vm_quota_for_user(s, "alice")
    assert limit == 10


@pytest.mark.asyncio
async def test_user_override_beats_tenant(sm):
    await _seed_basic(sm, tenant_quota=10, user_quota=2)
    async with sm() as s:
        limit = await resolve_vm_quota_for_user(s, "alice")
    assert limit == 2


@pytest.mark.asyncio
async def test_unknown_user_returns_default(sm):
    await _seed_basic(sm)
    async with sm() as s:
        limit = await resolve_vm_quota_for_user(s, "ghost")
    assert limit == DEFAULT_VMS_PER_USER


# ---------------------------------------------------------- count active


@pytest.mark.asyncio
async def test_count_excludes_retired_and_lost(sm):
    await _seed_basic(sm)
    await _add_active_vms(sm, "alice", 2, state="running")
    await _add_active_vms(sm, "alice", 1, state="retired")
    await _add_active_vms(sm, "alice", 1, state="lost")
    await _add_active_vms(sm, "alice", 1, state="cleaned")
    async with sm() as s:
        count = await count_active_vms_for_user(s, "alice")
    assert count == 2


@pytest.mark.asyncio
async def test_count_includes_failed(sm):
    """Failed VMs count against quota — otherwise a noisy user can spam
    failed deploys to bypass the limit."""
    await _seed_basic(sm)
    await _add_active_vms(sm, "alice", 1, state="running")
    await _add_active_vms(sm, "alice", 2, state="failed")
    async with sm() as s:
        count = await count_active_vms_for_user(s, "alice")
    assert count == 3


# ---------------------------------------------------------- check_user_vm_quota


@pytest.mark.asyncio
async def test_check_passes_when_under_limit(sm):
    await _seed_basic(sm)
    await _add_active_vms(sm, "alice", 2)
    async with sm() as s:
        await check_user_vm_quota(s, owner_id="alice", extra_requested=1)


@pytest.mark.asyncio
async def test_check_raises_at_limit(sm):
    await _seed_basic(sm)
    await _add_active_vms(sm, "alice", 3)  # already at default 3
    async with sm() as s:
        with pytest.raises(QuotaExceededError) as exc_info:
            await check_user_vm_quota(s, owner_id="alice", extra_requested=1)
    assert exc_info.value.current == 3
    assert exc_info.value.limit == 3
    assert exc_info.value.extra_requested == 1


@pytest.mark.asyncio
async def test_batch_check_with_multiple_owners(sm):
    await _seed_basic(sm)
    await _add_active_vms(sm, "alice", 2)
    async with sm() as s:
        # alice has 2, bob is fresh; default 3 each. Adding 1 alice + 1 bob OK.
        s.add(User(id="bob", email="b@x", display_name="B", tenant_id="t-a"))
        await s.commit()
        await check_batch_quota(s, owner_ids=["alice", "bob"])


@pytest.mark.asyncio
async def test_batch_check_counts_duplicate_owners(sm):
    """4 VMs for alice with default 3 should fail (alice has 0 active, but
    4 in one batch > 3 limit)."""
    await _seed_basic(sm)
    async with sm() as s:
        with pytest.raises(QuotaExceededError) as exc_info:
            await check_batch_quota(s, owner_ids=["alice", "alice", "alice", "alice"])
    assert exc_info.value.owner_id == "alice"
    assert exc_info.value.extra_requested == 4


# ---------------------------------------------------------- HTTP wire (429)


@pytest_asyncio.fixture
async def seeded_with_signed_image(engine):
    """deployments POST needs both a signed image and a tenant/user."""
    from agent_platform_control.db.models import ImageVersion

    sm_ = async_sessionmaker(engine, expire_on_commit=False)
    async with sm_() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(
            ImageVersion(
                version="v0.1.0",
                ova_sha256="a" * 64,
                signature_b64=None,  # legacy — accepted without pubkey
            )
        )
        await s.commit()
    return sm_


@pytest.mark.asyncio
async def test_post_deployments_returns_429_at_limit(client, seeded_with_signed_image):
    """End-to-end: alice already has 3 running VMs → POST returns 429."""
    sm = seeded_with_signed_image
    async with sm() as s:
        for i in range(3):
            s.add(
                VM(
                    id=f"vm-{i}",
                    name=f"vm-{i}",
                    tenant_id="t-a",
                    owner_id="alice",
                    image_version="v0.1.0",
                    state="running",
                )
            )
        await s.commit()

    body = {
        "tenant_id": "t-a",
        "template": "[t] x.vmtx",
        "image_version": "v0.1.0",
        "items": [{"owner_id": "alice", "intended_name": "vm-x"}],
    }
    r = await client.post(
        "/v1/deployments",
        json=body,
        headers={"Authorization": "Bearer dev-admin-token-CHANGE-ME"},
    )
    assert r.status_code == 429, r.text
    detail = r.json()["detail"]
    assert "alice" in detail
    assert "Decommission" in detail
