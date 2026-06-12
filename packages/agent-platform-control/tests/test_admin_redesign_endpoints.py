"""Tests for the 7 new admin endpoints added by R-2 (docs/architecture/21 §3).

Covers:
- shape of /admin/overview
- single-vCenter behavior via env (M1 limitation per decision 4-vCenter)
- /admin/vcenters/{name}/{health,inventory,templates} shapes + 404 paths
- /admin/components/health catalog (1 self HTTP + library + cli + placeholder rows)
- /admin/events filters + limit + ordering
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from agent_platform_control.db.models import (
    AuditView,
    Deployment,
    Tenant,
    TokenUsage,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN_TOKEN = "dev-admin-token-CHANGE-ME"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.mark.asyncio
async def test_overview_shape_with_empty_db(client):
    r = await client.get("/admin/overview", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["control_plane"]["status"] == "ok"
    assert body["vcenter"]["status"] == "deferred"
    counts = body["counts"]
    assert counts == {
        "vms_total": 0,
        "vms_running": 0,
        "deployments_total": 0,
        "deployments_provisioning": 0,
        "tokens_today": 0,
    }


@pytest.mark.asyncio
async def test_overview_counts_active_deployments(client, engine):
    """deployments_provisioning must count the real active states.

    Regression: the query filtered on state == "provisioning", which is not in
    the Deployment state set (pending/running/completed/partially_failed/
    failed/cancelled) — the KPI was hardwired to 0.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        for i, state in enumerate(("pending", "running", "completed", "failed")):
            s.add(
                Deployment(
                    id=f"dep-{i}",
                    tenant_id="t-a",
                    requested_by="alice",
                    template="tpl",
                    image_version="v0.1.0",
                    state=state,
                    requested_count=1,
                )
            )
        await s.commit()

    r = await client.get("/admin/overview", headers=AUTH)
    assert r.status_code == 200
    counts = r.json()["counts"]
    assert counts["deployments_total"] == 4
    assert counts["deployments_provisioning"] == 2  # pending + running


@pytest.mark.asyncio
async def test_overview_tokens_today_includes_midnight_row(client, engine):
    """A usage row stamped exactly at today 00:00:00.000000 must count.

    Regression: the day cutoff was now().replace(hour=0, minute=0, second=0)
    without microsecond=0, so rows in the first sub-second of the day fell
    before the cutoff.
    """
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="t-a",
                agent="goose",
                model="m",
                in_tokens=3,
                out_tokens=4,
                ts=midnight,
            )
        )
        await s.commit()

    r = await client.get("/admin/overview", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["counts"]["tokens_today"] == 7


@pytest.mark.asyncio
async def test_vcenters_empty_when_no_env(client, monkeypatch):
    for var in (
        "AGENT_PLATFORM_VSPHERE_URL",
        "AGENT_PLATFORM_VSPHERE_HOST",
        "AGENT_PLATFORM_VSPHERE_URL",
        "AGENT_PLATFORM_VSPHERE_USER",
        "AGENT_PLATFORM_VSPHERE_USER",
    ):
        monkeypatch.delenv(var, raising=False)
    r = await client.get("/admin/vcenters", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body == {"vcenters": [], "_single_only": True}


@pytest.mark.asyncio
async def test_vcenters_lists_single_target_from_env(client, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_URL", "vcsa-01.example.com")
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_USER", "svc-agent-platform@vsphere.local")
    r = await client.get("/admin/vcenters", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["vcenters"]) == 1
    vc = body["vcenters"][0]
    assert vc["name"] == "default"
    assert vc["host"] == "vcsa-01.example.com"
    assert vc["user"] == "svc-agent-platform@vsphere.local"


@pytest.mark.asyncio
async def test_vcenter_inventory_502_when_unreachable(client, monkeypatch):
    """R-3: real pyVmomi call now. Unreachable host yields a structured 502."""
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_URL", "definitely-not-a-real-host.invalid")
    r = await client.get("/admin/vcenters/default/inventory", headers=AUTH)
    assert r.status_code == 502
    assert "vCenter inventory error" in r.json()["detail"]


@pytest.mark.asyncio
async def test_vcenter_inventory_happy_path_mocked(client, monkeypatch):
    """R-3: with _inventory_sync mocked, the endpoint returns aggregated lists + counts."""
    from agent_platform_control.api.admin import vcenters as vc_mod

    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_URL", "vcsa-01.example.com")

    def fake_inventory_sync(_target):
        return {
            "hosts": [{"name": "esx-01"}, {"name": "esx-02"}],
            "clusters": [{"name": "cluster-prod"}],
            "datastores": [{"name": "ds-ssd"}, {"name": "ds-nfs"}],
            "networks": [{"name": "VM-Network"}],
        }

    monkeypatch.setattr(vc_mod, "_inventory_sync", fake_inventory_sync)
    # Bust cache from any earlier test run.
    vc_mod._INVENTORY_CACHE.clear()

    r = await client.get("/admin/vcenters/default/inventory", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False
    assert body["counts"] == {"hosts": 2, "clusters": 1, "datastores": 2, "networks": 1}
    assert body["hosts"][0]["name"] == "esx-01"

    # Second call hits cache.
    r2 = await client.get("/admin/vcenters/default/inventory", headers=AUTH)
    assert r2.json()["cached"] is True


@pytest.mark.asyncio
async def test_vcenter_404_for_unknown_name(client, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_VSPHERE_URL", "vcsa-01.example.com")
    r = await client.get("/admin/vcenters/other/health", headers=AUTH)
    assert r.status_code == 404
    assert "not configured" in r.json()["detail"]


@pytest.mark.asyncio
async def test_components_health_catalog(client):
    r = await client.get("/admin/components/health", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    comps = body["components"]
    ids = [c["id"] for c in comps]
    # Catalog order is locked: c1 first, then 6 others in CLAUDE.md naming order.
    assert ids == ["c1", "c5", "c6", "c7", "c18", "c19", "c20"]
    self_row = next(c for c in comps if c["id"] == "c1")
    assert self_row["status"] == "ok"
    # Library / cli / placeholder all surface as not_a_service in the response.
    for cid in ("c6", "c7", "c18", "c19", "c20"):
        row = next(c for c in comps if c["id"] == cid)
        assert row["status"] == "not_a_service"


@pytest.mark.asyncio
async def test_components_health_c5_unknown_when_no_url(client, monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_LLM_GATEWAY_URL", raising=False)
    r = await client.get("/admin/components/health", headers=AUTH)
    assert r.status_code == 200
    c5 = next(c for c in r.json()["components"] if c["id"] == "c5")
    assert c5["status"] == "unknown"
    assert "AGENT_PLATFORM_LLM_GATEWAY_URL" in c5["note"]


@pytest.mark.asyncio
async def test_events_returns_recent_audit_rows(client, engine):
    """Seed three AuditView rows; events endpoint returns them newest-first."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add_all(
            [
                AuditView(
                    actor="alice",
                    operation="vm.power_on",
                    resource="vm-1",
                    params={},
                    result="success",
                    ts=now - timedelta(minutes=5),
                ),
                AuditView(
                    actor="bob",
                    operation="vm.power_off",
                    resource="vm-2",
                    params={},
                    result="success",
                    ts=now - timedelta(minutes=2),
                ),
                AuditView(
                    actor="alice",
                    operation="vm.clone",
                    resource="vm-3",
                    params={"template": "ubuntu-22"},
                    result="failure",
                    ts=now,
                ),
            ]
        )
        await s.commit()

    r = await client.get("/admin/events?limit=2", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 2
    assert len(body["events"]) == 2
    # Most recent first.
    assert body["events"][0]["operation"] == "vm.clone"
    assert body["events"][0]["result"] == "failure"


@pytest.mark.asyncio
async def test_events_filters_by_since(client, engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add_all(
            [
                AuditView(
                    actor="alice",
                    operation="old",
                    resource="r1",
                    params={},
                    result="success",
                    ts=now - timedelta(hours=2),
                ),
                AuditView(
                    actor="alice",
                    operation="new",
                    resource="r2",
                    params={},
                    result="success",
                    ts=now,
                ),
            ]
        )
        await s.commit()
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    r = await client.get("/admin/events", params={"since": one_hour_ago}, headers=AUTH)
    assert r.status_code == 200
    ops = [e["operation"] for e in r.json()["events"]]
    assert ops == ["new"]


@pytest.mark.asyncio
async def test_redesign_endpoints_require_admin(client):
    """All seven new endpoints inherit the package-level admin gate."""
    for path in [
        "/admin/overview",
        "/admin/vcenters",
        "/admin/vcenters/default/health",
        "/admin/vcenters/default/inventory",
        "/admin/vcenters/default/templates",
        "/admin/components/health",
        "/admin/events",
    ]:
        r = await client.get(path)
        assert r.status_code == 401, f"{path} should be 401 without auth, got {r.status_code}"
