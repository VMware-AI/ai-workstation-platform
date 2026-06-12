"""Auth + admin RBAC tests — relies on default settings.admin_api_token."""

from __future__ import annotations

import pytest

ADMIN_TOKEN = "dev-admin-token-CHANGE-ME"  # matches config.py default


@pytest.mark.asyncio
async def test_admin_endpoints_require_credentials(client):
    r = await client.get("/admin/vms")
    assert r.status_code == 401
    assert "credentials" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_user_role_blocked_from_admin(client):
    r = await client.get("/admin/vms", headers={"X-User": "alice"})
    assert r.status_code == 403
    assert "admin" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_token_passes(client):
    r = await client.get("/admin/vms", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["_stub"] is True
    assert body["caller"] == "admin"


@pytest.mark.asyncio
async def test_invalid_bearer_rejected(client):
    r = await client.get("/admin/vms", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_tenants_endpoint(client):
    r = await client.get("/admin/tenants", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["_stub"] is True


@pytest.mark.asyncio
async def test_admin_audit_with_limit_param(client):
    r = await client.get(
        "/admin/audit?limit=50",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["limit"] == 50


def test_production_safety_blocks_dev_secrets():
    """Production posture (fake auth off) must reject committed dev defaults.

    Regression for PR-review #57 (default admin token) / #81 (default Fernet
    key) — no startup fail-fast existed.
    """
    from agent_platform_control.config import Settings

    prod = Settings(enable_fake_auth=False)
    problems = prod.production_safety_problems()
    assert problems, "dev defaults must be flagged when fake auth is off"
    assert any("ADMIN_API_TOKEN" in p for p in problems)
    assert any("FERNET" in p for p in problems)


def test_dev_posture_allows_defaults():
    """Dev/test posture (fake auth on) keeps the committed defaults silently."""
    from agent_platform_control.config import Settings

    dev = Settings(enable_fake_auth=True)
    assert dev.production_safety_problems() == []


def test_dev_fernet_key_is_valid_rebranded_placeholder():
    """The committed dev Fernet key must be a usable 32-byte placeholder and
    carry no stale 'lobster' codename (codename purge, 2026-06-09)."""
    import base64

    from agent_platform_control.config import _DEV_FERNET_KEY
    from cryptography.fernet import Fernet

    raw = base64.urlsafe_b64decode(_DEV_FERNET_KEY)
    assert len(raw) == 32  # Fernet requires exactly 32 key bytes
    assert b"agent-platform" in raw
    assert b"lobster" not in raw
    # Must actually construct a working Fernet (round-trip sanity).
    f = Fernet(_DEV_FERNET_KEY.encode("ascii"))
    assert f.decrypt(f.encrypt(b"x")) == b"x"


def test_secure_by_default_no_env(monkeypatch):
    """SEC-2: the bare compiled default (no env, no .env, no kwargs) is the
    SECURE posture — fake auth off, committed dev secrets flagged. A deploy
    that forgot to configure auth refuses to boot instead of trusting the
    committed default admin token.
    """
    from agent_platform_control.config import Settings

    # conftest sets AGENT_PLATFORM_ENABLE_FAKE_AUTH=1, and a dev .env sets it
    # true; bypass both (_env_file=None + delenv) to see the compiled default.
    monkeypatch.delenv("AGENT_PLATFORM_ENABLE_FAKE_AUTH", raising=False)
    default = Settings(_env_file=None)
    assert default.enable_fake_auth is False
    assert default.production_safety_problems(), "default posture must flag dev secrets"


@pytest.mark.asyncio
async def test_x_user_rejected_when_fake_auth_off(client, monkeypatch):
    """SEC-2: with fake auth off, an X-User header must NOT reach /admin —
    the only admin path is a real (non-default) bearer token."""
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_FAKE_AUTH", "0")
    from agent_platform_control import config

    config.get_settings.cache_clear()
    r = await client.get("/admin/vms", headers={"X-User": "attacker"})
    assert r.status_code == 401
