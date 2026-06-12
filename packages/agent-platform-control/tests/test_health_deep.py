"""Tests for /healthz/deep (PR-F F-2)."""

from __future__ import annotations

import pytest

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


@pytest.mark.asyncio
async def test_deep_health_requires_admin(client):
    r = await client.get("/healthz/deep")
    assert r.status_code == 401

    r = await client.get("/healthz/deep", headers={"X-User": "alice"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_deep_health_worker_failed_is_degraded(client):
    """Review MEDIUM: a worker that was WANTED (enable_worker) but failed to
    start (e.g. bad TTYD_ALLOW_CIDR) must surface as degraded — distinct from a
    deliberately-disabled worker that reports healthy."""
    app = client._transport.app
    app.state.runtime = {"worker": None, "worker_error": "TTYD_ALLOW_CIDR invalid: bad"}
    r = await client.get("/healthz/deep", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["components"]["worker"].startswith("failed")
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_deep_health_returns_component_states(client):
    r = await client.get("/healthz/deep", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    components = body["components"]
    assert components["db"] == "ok"
    # Without lifespan firing in the test ASGI transport, runtime is empty
    # → worker reports as "disabled"
    assert components["worker"] in ("disabled", "stopped", "running")
    # heartbeat_sweeper is wired (#350); without lifespan firing in the test
    # ASGI transport its runtime slot is empty → reports "disabled".
    assert components["heartbeat_sweeper"] in ("disabled", "stopped", "running")
    # cleanup_cron + token_expiry remain explicitly pending.
    assert components["cleanup_cron"] == "pending-pr-d"
    assert components["token_expiry_sweeper"] == "pending-pr-d"


@pytest.mark.asyncio
async def test_shallow_healthz_still_works(client):
    """The lightweight /healthz path stays unchanged."""
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
