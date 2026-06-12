"""/api/me/instances/{vm_id}/ttyd-url — W-3.1 mock + W-3.2 real direct wss."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import VM, Tenant, User
from sqlalchemy.ext.asyncio import async_sessionmaker

ALICE = {"X-User": "alice"}
BOB = {"X-User": "bob"}


@pytest_asyncio.fixture
async def seeded(engine):
    """Alice owns vm-a-1 (running); Bob owns vm-b-1."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="t-a"))
        s.add(User(id="bob", email="bob@example.com", display_name="Bob", tenant_id="t-a"))
        s.add(
            VM(
                id="vm-a-1",
                name="alice-running",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                ip_address="10.0.0.5",
                created_at=now - timedelta(hours=1),
            )
        )
        s.add(
            VM(
                id="vm-a-noip",
                name="alice-provisioning",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="provisioning",
                ip_address=None,
                created_at=now,
            )
        )
        s.add(
            VM(
                id="vm-b-1",
                name="bob-running",
                tenant_id="t-a",
                owner_id="bob",
                image_version="v0.1.0",
                state="running",
                created_at=now,
            )
        )
        await s.commit()
    return sm


def _clear_settings_cache() -> None:
    from agent_platform_control import config

    config.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ttyd_url_requires_auth(client, seeded):
    r = await client.get("/api/me/instances/vm-a-1/ttyd-url")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ttyd_url_returns_mock_when_configured(client, seeded, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_MOCK_URL", "ws://localhost:7681/ws")
    _clear_settings_cache()
    r = await client.get("/api/me/instances/vm-a-1/ttyd-url", headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "ws://localhost:7681/ws"}


@pytest.mark.asyncio
async def test_ttyd_url_503_when_not_configured(client, seeded):
    """Default config has neither real_mode nor a mock URL — endpoint must
    refuse loudly, not silently return an empty string. The detail must
    point operators at both knobs so they can pick one."""
    r = await client.get("/api/me/instances/vm-a-1/ttyd-url", headers=ALICE)
    assert r.status_code == 503
    body = r.json()
    assert "AGENT_PLATFORM_TTYD_MOCK_URL" in body["detail"]
    assert "AGENT_PLATFORM_TTYD_REAL_MODE" in body["detail"]


@pytest.mark.asyncio
async def test_ttyd_url_real_mode_returns_vm_ip_url(client, seeded, monkeypatch):
    """real_mode=True returns plain ws://<vm.ip>:7681/ws, ignoring the mock URL.

    Scheme is intentionally ``ws://`` not ``wss://`` — ttyd's systemd unit
    does not terminate TLS. Browsers loading the portal over https will hit
    a mixed-content block; staging must put a reverse-proxy in front.
    """
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_REAL_MODE", "true")
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_MOCK_URL", "ws://should-be-ignored/ws")
    _clear_settings_cache()
    r = await client.get("/api/me/instances/vm-a-1/ttyd-url", headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "ws://10.0.0.5:7681/ws"}


@pytest.mark.asyncio
async def test_ttyd_url_real_mode_503_when_no_ip(client, seeded, monkeypatch):
    """real_mode=True + VM still provisioning (no IP) → 503 telling the
    caller to retry once /api/me/instances reports an IP."""
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_REAL_MODE", "true")
    _clear_settings_cache()
    r = await client.get("/api/me/instances/vm-a-noip/ttyd-url", headers=ALICE)
    assert r.status_code == 503
    assert "no IP yet" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ttyd_url_real_mode_respects_custom_port(client, seeded, monkeypatch):
    """ttyd_port override applies (rare — only for ops that re-bind ttyd)."""
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_REAL_MODE", "true")
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_PORT", "9000")
    _clear_settings_cache()
    r = await client.get("/api/me/instances/vm-a-1/ttyd-url", headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "ws://10.0.0.5:9000/ws"}


@pytest.mark.asyncio
async def test_ttyd_url_404_for_other_users_vm(client, seeded, monkeypatch):
    """Owner check must happen before mock fallback so cross-tenant
    probing fails with a clear 404, not a 503."""
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_MOCK_URL", "ws://localhost:7681/ws")
    _clear_settings_cache()
    r = await client.get("/api/me/instances/vm-b-1/ttyd-url", headers=ALICE)
    assert r.status_code == 404
    assert "vm-b-1" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ttyd_url_404_for_nonexistent_vm(client, seeded, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_TTYD_MOCK_URL", "ws://localhost:7681/ws")
    _clear_settings_cache()
    r = await client.get("/api/me/instances/vm-does-not-exist/ttyd-url", headers=ALICE)
    assert r.status_code == 404
