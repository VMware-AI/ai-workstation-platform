"""Tests for /v1/deployments/{id}/items/{item_id}/cloud-init-log (R-5)."""

from __future__ import annotations

import pytest
from agent_platform_control.db.models import Deployment, DeploymentItem, Tenant, User
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN_TOKEN = "dev-admin-token-CHANGE-ME"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


async def _seed_one(engine) -> tuple[str, int]:
    """Insert tenant + user + deployment + 1 item; return (deployment_id, item_id)."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t1", display_name="Tenant 1"))
        s.add(
            User(
                id="alice",
                email="alice@example.com",
                display_name="Alice",
                tenant_id="t1",
            )
        )
        dep = Deployment(
            id="dep-test-1",
            tenant_id="t1",
            requested_by="alice",
            template="ubuntu-22-base",
            image_version="2026.05.01",
            state="provisioning",
            requested_count=1,
            succeeded_count=0,
            failed_count=0,
        )
        s.add(dep)
        await s.flush()
        item = DeploymentItem(
            deployment_id=dep.id,
            owner_id="alice",
            intended_name="alice-ws",
            state="pending",
            vm_id=None,
            attempts=0,
            user_token_enc="dev-encrypted-token",
            user_token_hash=f"sha256-{dep.id}-1",
        )
        s.add(item)
        await s.commit()
        await s.refresh(item)
        return dep.id, item.id


@pytest.mark.asyncio
async def test_cloud_init_log_404_for_missing_item(client):
    r = await client.get("/v1/deployments/nope/items/9999/cloud-init-log", headers=AUTH)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cloud_init_log_available_false_when_no_artifact(
    client, engine, tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_PLATFORM_CLOUD_INIT_LOG_DIR", str(tmp_path))
    dep_id, item_id = await _seed_one(engine)
    r = await client.get(f"/v1/deployments/{dep_id}/items/{item_id}/cloud-init-log", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["deployment_id"] == dep_id
    assert body["item_id"] == item_id
    assert "expected_path" in body
    assert str(tmp_path) in body["expected_path"]


@pytest.mark.asyncio
async def test_cloud_init_log_returns_content_when_present(client, engine, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_CLOUD_INIT_LOG_DIR", str(tmp_path))
    dep_id, item_id = await _seed_one(engine)
    log_dir = tmp_path / dep_id
    log_dir.mkdir()
    sample = "Cloud-init v. 23.4 running 'init-local' at Mon, 30 May 2026 14:23:00\n" * 5
    (log_dir / f"{item_id}.log").write_text(sample)

    r = await client.get(f"/v1/deployments/{dep_id}/items/{item_id}/cloud-init-log", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["bytes"] == len(sample)
    assert "Cloud-init" in body["log"]
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_cloud_init_log_truncates_to_tail(client, engine, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_CLOUD_INIT_LOG_DIR", str(tmp_path))
    dep_id, item_id = await _seed_one(engine)
    log_dir = tmp_path / dep_id
    log_dir.mkdir()
    # 20 KB — must trigger 8 KB tail.
    sample = "x" * 20000
    (log_dir / f"{item_id}.log").write_text(sample)

    r = await client.get(f"/v1/deployments/{dep_id}/items/{item_id}/cloud-init-log", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert len(body["log"]) == 8192


@pytest.mark.asyncio
async def test_cloud_init_log_requires_admin(client):
    r = await client.get("/v1/deployments/x/items/1/cloud-init-log")
    assert r.status_code == 401
