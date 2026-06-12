"""/api/heartbeat — VM liveness signal (PR-D D-6, decision 16)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import VM, Deployment, DeploymentItem, Tenant, User
from sqlalchemy.ext.asyncio import async_sessionmaker


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


VALID_TOKEN_A = "hbt_alice_test"
UNKNOWN_TOKEN = "hbt_ghost"


@pytest_asyncio.fixture
async def seeded(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(
            VM(
                id="vm-1",
                name="alice-vm",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                created_at=now,
            )
        )
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="tpl",
                image_version="v0.1.0",
                state="completed",
                requested_count=1,
                succeeded_count=1,
            )
        )
        s.add(
            DeploymentItem(
                id=1,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="alice-vm",
                vm_id="vm-1",
                user_token_enc="e1",
                user_token_hash="h1",
                state="powered_on",
                heartbeat_token_hash=_hash(VALID_TOKEN_A),
            )
        )
        await s.commit()
    return sm


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_heartbeat_updates_last_heartbeat_at(client, seeded):
    r = await client.post("/api/heartbeat", headers=_bearer(VALID_TOKEN_A))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deployment_item_id"] == 1
    assert body["vm_id"] == "vm-1"

    async with seeded() as s:
        vm = await s.get(VM, "vm-1")
    assert vm.last_heartbeat_at is not None


@pytest.mark.asyncio
async def test_heartbeat_rejects_unknown_token(client, seeded):
    r = await client.post("/api/heartbeat", headers=_bearer(UNKNOWN_TOKEN))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_heartbeat_rejects_missing_bearer(client, seeded):
    r = await client.post("/api/heartbeat")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_heartbeat_restores_lost_state_to_running(client, seeded):
    """A heartbeat from a 'lost' VM means it's back; reset to running."""
    async with seeded() as s:
        vm = await s.get(VM, "vm-1")
        vm.state = "lost"
        await s.commit()

    await client.post("/api/heartbeat", headers=_bearer(VALID_TOKEN_A))
    async with seeded() as s:
        vm = await s.get(VM, "vm-1")
    assert vm.state == "running"


# ============================================================== sweeper unit tests


@pytest.mark.asyncio
async def test_sweeper_marks_unhealthy_at_5_minutes(seeded):
    from agent_platform_control.orchestrator.cleanup_cron import heartbeat_sweep

    sm = seeded
    async with sm() as s:
        vm = await s.get(VM, "vm-1")
        vm.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=7)
        await s.commit()

    unhealthy, lost = await heartbeat_sweep(sm)
    assert unhealthy == 1
    assert lost == 0
    async with sm() as s:
        vm = await s.get(VM, "vm-1")
    assert vm.state == "unhealthy"


@pytest.mark.asyncio
async def test_sweeper_marks_lost_at_30_minutes(seeded):
    from agent_platform_control.orchestrator.cleanup_cron import heartbeat_sweep

    sm = seeded
    async with sm() as s:
        vm = await s.get(VM, "vm-1")
        vm.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=45)
        await s.commit()

    _, lost = await heartbeat_sweep(sm)
    assert lost == 1
    async with sm() as s:
        vm = await s.get(VM, "vm-1")
    assert vm.state == "lost"


@pytest.mark.asyncio
async def test_sweeper_ignores_vms_never_heartbeated(seeded):
    """VM with last_heartbeat_at=None is the worker's responsibility."""
    from agent_platform_control.orchestrator.cleanup_cron import heartbeat_sweep

    sm = seeded
    unhealthy, lost = await heartbeat_sweep(sm)
    assert unhealthy == 0
    assert lost == 0


@pytest.mark.asyncio
async def test_sweeper_is_idempotent(seeded):
    """Re-running on an already-flagged VM doesn't double-count."""
    from agent_platform_control.orchestrator.cleanup_cron import heartbeat_sweep

    sm = seeded
    async with sm() as s:
        vm = await s.get(VM, "vm-1")
        vm.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=45)
        await s.commit()

    first = await heartbeat_sweep(sm)
    second = await heartbeat_sweep(sm)
    assert first == (0, 1)
    assert second == (0, 0)
