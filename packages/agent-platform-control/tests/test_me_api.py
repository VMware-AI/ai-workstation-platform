"""/api/me/* — per-user portal endpoints (instances + usage)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import VM, Tenant, TokenUsage, User
from sqlalchemy.ext.asyncio import async_sessionmaker

ALICE = {"X-User": "alice"}
BOB = {"X-User": "bob"}
ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


@pytest_asyncio.fixture
async def seeded(engine):
    """Two users in tenant t-a, each with VMs + usage rows."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="t-a"))
        s.add(User(id="bob", email="bob@example.com", display_name="Bob", tenant_id="t-a"))

        # Alice: 2 active VMs + 1 retired (should be hidden)
        s.add(
            VM(
                id="vm-a-1",
                name="alice-running",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                created_at=now - timedelta(hours=2),
            )
        )
        s.add(
            VM(
                id="vm-a-2",
                name="alice-provisioning",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="provisioning",
                created_at=now - timedelta(hours=1),
            )
        )
        s.add(
            VM(
                id="vm-a-3",
                name="alice-retired",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.0.9",
                state="retired",
                created_at=now - timedelta(days=10),
                retired_at=now - timedelta(days=1),
            )
        )
        # Bob: 1 stopping VM
        s.add(
            VM(
                id="vm-b-1",
                name="bob-stopping",
                tenant_id="t-a",
                owner_id="bob",
                image_version="v0.1.0",
                state="stopping",
                created_at=now,
            )
        )

        # Usage — Alice yesterday + today
        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="t-a",
                agent="claude-code",
                model="claude-sonnet-4-6",
                in_tokens=1000,
                out_tokens=200,
                duration_s=1.5,
                ts=now,
            )
        )
        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="t-a",
                agent="goose",
                model="claude-sonnet-4-6",
                in_tokens=300,
                out_tokens=50,
                duration_s=0.8,
                ts=now - timedelta(days=1),
            )
        )
        # Old row outside default 30-day window
        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="t-a",
                agent="qcoder",
                model="claude-sonnet-4-6",
                in_tokens=99999,
                out_tokens=99999,
                duration_s=10.0,
                ts=now - timedelta(days=60),
            )
        )
        # Bob's usage — must not leak into Alice's totals
        s.add(
            TokenUsage(
                user_id="bob",
                tenant_id="t-a",
                agent="claude-code",
                model="claude-sonnet-4-6",
                in_tokens=500,
                out_tokens=100,
                duration_s=1.0,
                ts=now,
            )
        )

        await s.commit()
    return sm


# ---------- /api/me/instances ----------


@pytest.mark.asyncio
async def test_instances_requires_auth(client, seeded):
    r = await client.get("/api/me/instances")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_instances_returns_only_own_active_vms(client, seeded):
    r = await client.get("/api/me/instances", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    names = sorted(a["name"] for a in body["agents"])
    assert names == ["alice-provisioning", "alice-running"]  # retired hidden
    states = {a["name"]: a["state"] for a in body["agents"]}
    assert states["alice-running"] == "running"
    assert states["alice-provisioning"] == "pending"
    # template carries image_version
    assert all(a["template"] == "v0.1.0" for a in body["agents"])


@pytest.mark.asyncio
async def test_instances_user_isolation(client, seeded):
    """Bob must not see Alice's VMs and vice versa."""
    r = await client.get("/api/me/instances", headers=BOB)
    assert r.status_code == 200
    body = r.json()
    assert len(body["agents"]) == 1
    assert body["agents"][0]["name"] == "bob-stopping"
    assert body["agents"][0]["state"] == "running"  # stopping → running

    r = await client.get("/api/me/instances", headers=ALICE)
    bob_vms = [a for a in r.json()["agents"] if a["id"].startswith("vm-b-")]
    assert bob_vms == []


@pytest.mark.asyncio
async def test_instances_empty_for_user_with_no_vms(client, seeded):
    """Admin token resolves to user_id='admin' which has no VMs seeded."""
    r = await client.get("/api/me/instances", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"agents": []}


# ---------- /api/me/usage ----------


@pytest.mark.asyncio
async def test_usage_requires_auth(client, seeded):
    r = await client.get("/api/me/usage")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_usage_default_30_days_aggregates_own_only(client, seeded):
    r = await client.get("/api/me/usage", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    # Default window is 30 days
    assert len(body["days"]) == 30
    # Alice: today 1200 + yesterday 350 = 1550; 60-day-old row excluded
    assert body["total_tokens"] == 1550
    # Zero-fill in between
    nonzero = [d for d in body["days"] if d["tokens"] > 0]
    assert len(nonzero) == 2
    assert sum(d["tokens"] for d in nonzero) == 1550


@pytest.mark.asyncio
async def test_usage_user_isolation(client, seeded):
    """Bob's totals must not include Alice's traffic."""
    r = await client.get("/api/me/usage", headers=BOB)
    assert r.status_code == 200
    body = r.json()
    assert body["total_tokens"] == 600  # 500 + 100


@pytest.mark.asyncio
async def test_usage_days_param_clamping(client, seeded):
    r = await client.get("/api/me/usage?days=0", headers=ALICE)
    assert r.status_code == 422  # ge=1

    r = await client.get("/api/me/usage?days=500", headers=ALICE)
    assert r.status_code == 422  # le=365

    r = await client.get("/api/me/usage?days=7", headers=ALICE)
    assert r.status_code == 200
    assert len(r.json()["days"]) == 7


@pytest.mark.asyncio
async def test_usage_includes_old_row_when_window_wide_enough(client, seeded):
    r = await client.get("/api/me/usage?days=90", headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 90
    # 60-day-old row (99999 + 99999) now included
    assert body["total_tokens"] == 1550 + 99999 + 99999


# ---------- W-5: fileshare_path + quota ----------


@pytest.mark.asyncio
async def test_instances_fileshare_path_null_when_base_unset(client, seeded):
    """Default config has no fileshare_base; portal renders '—' rather than a broken UNC."""
    r = await client.get("/api/me/instances", headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert all(a["fileshare_path"] is None for a in body["agents"])


@pytest.mark.asyncio
async def test_instances_fileshare_path_unc_when_base_set(client, seeded, monkeypatch):
    """When AGENT_PLATFORM_FILESHARE_BASE=fs.example.local, each agent gets the UNC path."""
    monkeypatch.setenv("AGENT_PLATFORM_FILESHARE_BASE", "fs.example.local")
    # clean_settings_cache is autouse, but we re-clear after the env mutation so
    # the next get_settings() call picks up the new value.
    from agent_platform_control import config

    config.get_settings.cache_clear()

    r = await client.get("/api/me/instances", headers=ALICE)
    assert r.status_code == 200
    paths = [a["fileshare_path"] for a in r.json()["agents"]]
    assert all(p == r"\\fs.example.local\u\alice\workspace" for p in paths)


@pytest.mark.asyncio
async def test_usage_exposes_quota_total_and_used(client, seeded):
    """Portal Quota badge reads quota_total/quota_used (M1 soft default)."""
    r = await client.get("/api/me/usage", headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert body["quota_total"] == 1_000_000
    assert body["quota_used"] == body["total_tokens"]


@pytest.mark.asyncio
async def test_usage_quota_total_respects_env_override(client, seeded, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_QUOTA_TOTAL_TOKENS", "500000")
    from agent_platform_control import config

    config.get_settings.cache_clear()

    r = await client.get("/api/me/usage", headers=ALICE)
    assert r.status_code == 200
    assert r.json()["quota_total"] == 500_000
