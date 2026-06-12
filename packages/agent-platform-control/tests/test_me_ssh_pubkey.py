"""GET / PUT /api/me/ssh-pubkey — portal SSH pubkey upload (PR-C, decision 4).

Decision 4 lock 2026-05-29: ``ssh_authorized_keys`` is a per-VM secret
distributed via the cloud-init token exchange. The source of truth for the
underlying pubkey is the user's portal profile — they upload once, and every
new VM gets the latest copy at provisioning time.

These tests cover the portal-facing edge of the secret flow only. The
provisioning-time copy from ``users.ssh_pubkey`` to the per-VM Vaultwarden
entry is covered by C-4 tests.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_control.db.models import Tenant, User
from sqlalchemy.ext.asyncio import async_sessionmaker

ALICE = {"X-User": "alice"}
BOB = {"X-User": "bob"}

# A real OpenSSH Ed25519 public key (generated for this test; not used in prod).
VALID_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA3xQHNV6vEX4l6nz/lOJqEi5DPwI+Z6r8mFZAvT/oR2 alice@laptop"
)
SECOND_VALID_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDxbLPI4i0E4Sn4yqGq0SLfYHqcgwzc7DmDh8Xq8VuhV alice@desktop"
)
PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQ\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


@pytest_asyncio.fixture
async def seeded_users(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="alice@x", display_name="Alice", tenant_id="t-a"))
        s.add(User(id="bob", email="bob@x", display_name="Bob", tenant_id="t-a"))
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_get_unset_returns_null(client, seeded_users):
    r = await client.get("/api/me/ssh-pubkey", headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert body == {"pubkey": None, "fingerprint": None}


@pytest.mark.asyncio
async def test_put_then_get_roundtrip(client, seeded_users):
    r = await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": VALID_PUBKEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pubkey"] == VALID_PUBKEY
    assert body["fingerprint"] is not None
    assert body["fingerprint"].startswith("SHA256:")

    r = await client.get("/api/me/ssh-pubkey", headers=ALICE)
    assert r.status_code == 200
    assert r.json()["pubkey"] == VALID_PUBKEY


@pytest.mark.asyncio
async def test_put_overwrites_previous(client, seeded_users):
    await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": VALID_PUBKEY})
    r = await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": SECOND_VALID_PUBKEY})
    assert r.status_code == 200
    assert r.json()["pubkey"] == SECOND_VALID_PUBKEY


@pytest.mark.asyncio
async def test_put_rejects_private_key(client, seeded_users):
    r = await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": PRIVATE_KEY})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"].lower()
    # Educational error — must teach the user what they did wrong
    assert "private" in detail or "public key" in detail


@pytest.mark.asyncio
async def test_put_rejects_garbage(client, seeded_users):
    r = await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": "not a real key"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_rejects_too_long(client, seeded_users):
    # 5KB key — exceeds the 4KB sanity cap
    too_long = "ssh-ed25519 " + "A" * 5000
    r = await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": too_long})
    assert r.status_code in (400, 422), r.text


@pytest.mark.asyncio
async def test_alice_pubkey_isolated_from_bob(client, seeded_users):
    """Decision 14 spirit: one user's profile change must never leak to another."""
    await client.put("/api/me/ssh-pubkey", headers=ALICE, json={"pubkey": VALID_PUBKEY})
    r = await client.get("/api/me/ssh-pubkey", headers=BOB)
    assert r.status_code == 200
    assert r.json()["pubkey"] is None


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client, seeded_users):
    r = await client.get("/api/me/ssh-pubkey")
    assert r.status_code == 401
