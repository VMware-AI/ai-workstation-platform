"""/v1/deployments API tests — happy + validation + cancel."""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_control.db.models import DeploymentItem, ImageVersion, Tenant, User
from agent_platform_control.orchestrator.tokens import decrypt_user_token, hash_user_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


@pytest_asyncio.fixture
async def seeded(engine):
    """Seed tenant + 2 users so deployment creates don't fail FK.

    Also registers a legacy unsigned ImageVersion row so the PR-E
    signature check (api/deployments.py `_verify_image_version_or_fail`)
    accepts these tests as pre-PR-E data.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="t-a"))
        s.add(User(id="bob", email="bob@example.com", display_name="Bob", tenant_id="t-a"))
        s.add(
            ImageVersion(
                version="v0.1.0",
                ova_sha256="a" * 64,
                signature_b64=None,  # legacy fallback
            )
        )
        await s.commit()
    return sm


def _sample_payload(n: int = 2) -> dict:
    return {
        "tenant_id": "t-a",
        "template": "[templates] agent-platform-ubuntu22/agent-platform-ubuntu22.vmtx",
        "image_version": "v0.1.0",
        "items": [
            {"owner_id": owner, "intended_name": f"vm-{owner}-001"}
            for owner in (["alice", "bob"][:n])
        ],
    }


@pytest.mark.asyncio
async def test_create_requires_admin(client, seeded):
    r = await client.post("/v1/deployments", json=_sample_payload())
    assert r.status_code == 401

    r = await client.post("/v1/deployments", json=_sample_payload(), headers={"X-User": "alice"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_happy_path(client, seeded):
    r = await client.post("/v1/deployments", json=_sample_payload(2), headers=ADMIN)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "pending"
    assert body["counts"] == {"requested": 2, "succeeded": 0, "failed": 0}
    assert len(body["items"]) == 2
    assert all(it["state"] == "pending" for it in body["items"])
    assert all(it["vm_id"] is None for it in body["items"])

    async with seeded() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert len(items) == 2
        assert all(not it.user_token_enc.startswith("tok_") for it in items)
        for item in items:
            token = decrypt_user_token(item.user_token_enc)
            assert token.startswith("tok_")
            assert item.user_token_hash == hash_user_token(token)


@pytest.mark.asyncio
async def test_create_rejects_duplicate_names(client, seeded):
    payload = _sample_payload(2)
    payload["items"][1]["intended_name"] = payload["items"][0]["intended_name"]
    r = await client.post("/v1/deployments", json=payload, headers=ADMIN)
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_rejects_empty_items(client, seeded):
    payload = _sample_payload(2)
    payload["items"] = []
    r = await client.post("/v1/deployments", json=payload, headers=ADMIN)
    assert r.status_code == 422  # pydantic min_length=1


@pytest.mark.asyncio
async def test_get_404_when_unknown(client, seeded):
    r = await client.get("/v1/deployments/nonexistent", headers=ADMIN)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_returns_items(client, seeded):
    create = await client.post("/v1/deployments", json=_sample_payload(2), headers=ADMIN)
    dep_id = create.json()["id"]

    r = await client.get(f"/v1/deployments/{dep_id}", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == dep_id
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_list_filters(client, seeded):
    await client.post("/v1/deployments", json=_sample_payload(1), headers=ADMIN)
    await client.post("/v1/deployments", json=_sample_payload(2), headers=ADMIN)

    r = await client.get("/v1/deployments?tenant_id=t-a", headers=ADMIN)
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = await client.get("/v1/deployments?tenant_id=does-not-exist", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_cancel_pending_items(client, seeded):
    create = await client.post("/v1/deployments", json=_sample_payload(2), headers=ADMIN)
    dep_id = create.json()["id"]

    r = await client.post(f"/v1/deployments/{dep_id}/cancel", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "cancelled"
    assert all(it["state"] == "cancelled" for it in body["items"])


@pytest.mark.asyncio
async def test_cancel_unknown_404(client, seeded):
    r = await client.post("/v1/deployments/nonexistent/cancel", headers=ADMIN)
    assert r.status_code == 404
