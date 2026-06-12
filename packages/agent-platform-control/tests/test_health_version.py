"""/healthz /readyz /version smoke tests."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_ok(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_db_ok(client):
    r = await client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["db"] == "ok"


@pytest.mark.asyncio
async def test_version_contains_package_and_sha(client):
    r = await client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["package"] == "agent-platform-control"
    assert body["version"]
    assert "git_sha" in body  # may be 'unknown' off-tree but key must exist
