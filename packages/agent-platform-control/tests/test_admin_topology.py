"""Tests for /admin/vms/topology — W-2 circular topology backend.

Covers:
- empty DB → 1 vCenter node, 0 VMs, 0 edges
- with VMs seeded → 1 + N nodes, N edges, state propagated from VM row
- vCenter ``id``/``name`` reflects env var (configured vs unconfigured)
- requires admin auth (RBAC inherited from /admin)
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_control.db.models import VM, Tenant, User
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


@pytest_asyncio.fixture
async def seeded_vms(engine):
    """Seed 1 tenant + 1 user + 2 VMs in different states."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="t-a"))
        s.add(
            VM(
                id="vm-001",
                name="alice-goose-01",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                ip_address="10.0.0.5",
            )
        )
        s.add(
            VM(
                id="vm-002",
                name="alice-goose-02",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="provisioning",
            )
        )
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_topology_empty_db_unconfigured_vcenter(client, monkeypatch):
    for var in (
        "AGENT_PLATFORM_VCENTER_HOST",
        "AGENT_PLATFORM_VSPHERE_HOST",
        "AGENT_PLATFORM_VSPHERE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    r = await client.get("/admin/vms/topology", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["edges"] == []
    assert len(body["nodes"]) == 1
    center = body["nodes"][0]
    assert center["id"] == "vcenter:unconfigured"
    assert center["category"] == "vcenter"
    assert center["state"] == "online"
    assert center["tenant"] is None


@pytest.mark.asyncio
async def test_topology_with_env_vcenter_and_vms(client, monkeypatch, seeded_vms):
    monkeypatch.setenv("AGENT_PLATFORM_VCENTER_HOST", "vcsa-01.example.com")
    r = await client.get("/admin/vms/topology", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()

    # 1 vCenter + 2 VMs = 3 nodes; 2 edges (vCenter → each VM)
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) == 2

    center = next(n for n in body["nodes"] if n["category"] == "vcenter")
    assert center["id"] == "vcenter:default"
    assert center["name"] == "vcsa-01.example.com"

    vm_nodes = sorted(
        (n for n in body["nodes"] if n["category"] == "vm"),
        key=lambda n: n["id"],
    )
    assert vm_nodes[0]["id"] == "vm:vm-001"
    assert vm_nodes[0]["name"] == "alice-goose-01"
    assert vm_nodes[0]["state"] == "running"
    assert vm_nodes[0]["tenant"] == "t-a"

    assert vm_nodes[1]["state"] == "provisioning"

    # Every edge points from the center to a VM
    assert all(e["source"] == "vcenter:default" for e in body["edges"])
    edge_targets = sorted(e["target"] for e in body["edges"])
    assert edge_targets == ["vm:vm-001", "vm:vm-002"]


@pytest.mark.asyncio
async def test_topology_requires_admin(client):
    r = await client.get("/admin/vms/topology")
    # No credential at all → exactly 401 (403 is for an authenticated non-admin;
    # a vague `in (401, 403)` would mask an auth regression — harness #H-10).
    assert r.status_code == 401
