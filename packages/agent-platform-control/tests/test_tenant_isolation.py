"""Tenant isolation audit tests — Decision 14.

Two tenants (t-a, t-b), one user each (alice in t-a, bob in t-b), each with
a VM, a deployment, an upgrade, and token-usage rows. Prove that:

  - Alice (X-User: alice) reads ONLY t-a rows
  - Bob (X-User: bob) reads ONLY t-b rows
  - Cross-tenant reads via me.* endpoints return empty / their-own only

Admin endpoints (deployments / upgrades) are explicitly admin-bypass per
Decision 14 — they're not exercised here for isolation; admin acts on all
tenants by design and is gated by ``require_admin``.

Plus unit-level tests for :func:`tenant_scope` itself: model inference,
admin-only refusal, empty tenant_id refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import (
    VM,
    AuditView,
    Deployment,
    ImageVersion,
    Tenant,
    TokenUsage,
    Upgrade,
    User,
)
from agent_platform_control.db.tenant_scope import (
    ADMIN_ONLY_MODELS,
    TENANT_SCOPED_MODELS,
    TenantScopeError,
    requires_tenant_scope,
    tenant_scope,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ALICE = {"X-User": "alice"}
BOB = {"X-User": "bob"}
ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


# ---------------------------------------------------------------------------
# Fixture: two tenants, two users, one VM + deployment + upgrade + usage each.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def two_tenants(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        # tenants
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(Tenant(id="t-b", display_name="Tenant B"))
        # users
        s.add(User(id="alice", email="alice@a.example", display_name="Alice", tenant_id="t-a"))
        s.add(User(id="bob", email="bob@b.example", display_name="Bob", tenant_id="t-b"))
        # VMs (one per user, in their own tenant)
        s.add(
            VM(
                id="vm-a",
                name="alice-vm",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                created_at=now,
            )
        )
        s.add(
            VM(
                id="vm-b",
                name="bob-vm",
                tenant_id="t-b",
                owner_id="bob",
                image_version="v0.1.0",
                state="running",
                created_at=now,
            )
        )
        # Deployments (admin-bypass endpoints, but seeded for completeness)
        s.add(
            Deployment(
                id="dep-a",
                tenant_id="t-a",
                requested_by="alice",
                template="tpl-a",
                image_version="v0.1.0",
                state="completed",
                requested_count=1,
                succeeded_count=1,
                failed_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            Deployment(
                id="dep-b",
                tenant_id="t-b",
                requested_by="bob",
                template="tpl-b",
                image_version="v0.1.0",
                state="completed",
                requested_count=1,
                succeeded_count=1,
                failed_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        # Upgrades
        s.add(
            Upgrade(
                id="up-a",
                tenant_id="t-a",
                from_version="v0.1.0",
                to_version="v0.2.0",
                state="planned",
                started_by="alice",
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            Upgrade(
                id="up-b",
                tenant_id="t-b",
                from_version="v0.1.0",
                to_version="v0.2.0",
                state="planned",
                started_by="bob",
                created_at=now,
                updated_at=now,
            )
        )
        # Token usage — each user has a row in their own tenant
        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="t-a",
                agent="claude-code",
                model="claude-sonnet-4-6",
                in_tokens=100,
                out_tokens=50,
                duration_s=1.0,
                ts=now,
            )
        )
        s.add(
            TokenUsage(
                user_id="bob",
                tenant_id="t-b",
                agent="claude-code",
                model="claude-sonnet-4-6",
                in_tokens=200,
                out_tokens=80,
                duration_s=2.0,
                ts=now,
            )
        )
        await s.commit()

    # Crafted leak — a TokenUsage row referencing alice but tagged with t-b.
    # If tenant_scope works, /api/me/usage as Alice must NOT include this.
    async with sm() as s:
        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="t-b",  # WRONG tenant — should be filtered out
                agent="claude-code",
                model="claude-sonnet-4-6",
                in_tokens=999999,
                out_tokens=999999,
                duration_s=99.0,
                ts=now,
            )
        )
        # A VM owned by alice but tagged with t-b — same leak scenario.
        s.add(
            VM(
                id="vm-leak",
                name="alice-leak",
                tenant_id="t-b",  # WRONG tenant
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                created_at=now - timedelta(hours=1),
            )
        )
        await s.commit()
    return sm


# ---------------------------------------------------------------------------
# Audit tests — /api/me/instances
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_instances_alice_sees_only_tenant_a(client, two_tenants):
    r = await client.get("/api/me/instances", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [a["id"] for a in body["agents"]]
    # Alice owns vm-a (t-a) and vm-leak (t-b, crafted leak). Without tenant
    # scope she'd see both. With scope she only sees t-a.
    assert ids == ["vm-a"], f"expected only vm-a, got {ids}"


@pytest.mark.asyncio
async def test_me_instances_bob_sees_only_tenant_b(client, two_tenants):
    r = await client.get("/api/me/instances", headers=BOB)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [a["id"] for a in body["agents"]]
    assert ids == ["vm-b"], f"expected only vm-b, got {ids}"


@pytest.mark.asyncio
async def test_me_instances_no_cross_tenant_leak(client, two_tenants):
    """Neither user sees the other's VMs even via the deliberate-leak row."""
    r_alice = await client.get("/api/me/instances", headers=ALICE)
    r_bob = await client.get("/api/me/instances", headers=BOB)
    alice_ids = {a["id"] for a in r_alice.json()["agents"]}
    bob_ids = {a["id"] for a in r_bob.json()["agents"]}
    assert alice_ids.isdisjoint(bob_ids)
    assert "vm-b" not in alice_ids
    assert "vm-a" not in bob_ids
    assert "vm-leak" not in alice_ids  # the crafted t-b-tagged alice VM


# ---------------------------------------------------------------------------
# Audit tests — /api/me/usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_usage_alice_excludes_wrong_tenant_row(client, two_tenants):
    """Alice's usage must NOT include the 999999/999999 row tagged with t-b."""
    r = await client.get("/api/me/usage?days=2", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    # Alice's legit row is 100+50=150. The leaky t-b row would add 999999*2.
    assert body["total_tokens"] == 150, (
        f"tenant leak: expected 150, got {body['total_tokens']} — "
        "tenant_scope did not filter out the wrong-tenant row"
    )


@pytest.mark.asyncio
async def test_me_usage_bob_only_sees_own(client, two_tenants):
    r = await client.get("/api/me/usage?days=2", headers=BOB)
    assert r.status_code == 200
    body = r.json()
    assert body["total_tokens"] == 280  # 200 + 80


# ---------------------------------------------------------------------------
# tenant_scope unit tests
# ---------------------------------------------------------------------------


def test_tenant_scope_adds_where_clause():
    stmt = tenant_scope(select(VM), tenant_id="t-a", model=VM)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant_id" in sql
    assert "'t-a'" in sql


def test_tenant_scope_infers_model_from_select():
    """Caller can omit ``model`` for plain ``select(Model)``."""
    stmt = tenant_scope(select(VM), tenant_id="t-a")
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "vms.tenant_id" in sql


def test_tenant_scope_rejects_admin_only_model():
    with pytest.raises(TenantScopeError, match="admin-only"):
        tenant_scope(select(ImageVersion), tenant_id="t-a", model=ImageVersion)
    with pytest.raises(TenantScopeError, match="admin-only"):
        tenant_scope(select(AuditView), tenant_id="t-a", model=AuditView)


def test_tenant_scope_rejects_empty_tenant_id():
    """Empty tenant_id → not a no-op, an explicit error."""
    with pytest.raises(TenantScopeError, match="non-empty tenant_id"):
        tenant_scope(select(VM), tenant_id="", model=VM)


def test_tenant_scope_does_not_mutate_original_stmt():
    """Immutability: SQLAlchemy .where returns a copy; verify wrapper does too."""
    base = select(VM)
    base_sql = str(base.compile(compile_kwargs={"literal_binds": True}))
    scoped = tenant_scope(base, tenant_id="t-a", model=VM)
    after_sql = str(base.compile(compile_kwargs={"literal_binds": True}))
    # Compiled SQL of the original is unchanged (no new WHERE).
    assert base_sql == after_sql
    assert "WHERE" not in after_sql.upper()
    # The wrapped copy gained the filter.
    scoped_sql = str(scoped.compile(compile_kwargs={"literal_binds": True}))
    assert "WHERE" in scoped_sql.upper()
    assert "'t-a'" in scoped_sql


def test_requires_tenant_scope_classification():
    # Tenant-owned tables
    for m in TENANT_SCOPED_MODELS:
        assert requires_tenant_scope(m), f"{m.__name__} should require scope"
    # Admin-only
    for m in ADMIN_ONLY_MODELS:
        assert not requires_tenant_scope(m), f"{m.__name__} should NOT require scope"


def test_tenant_scope_preserves_existing_where_clauses():
    """tenant_scope() composes with existing .where() — both predicates ANDed."""
    stmt = tenant_scope(
        select(VM).where(VM.owner_id == "alice"),
        tenant_id="t-a",
        model=VM,
    )
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant_id" in sql
    assert "owner_id" in sql
    assert "'alice'" in sql
    assert "'t-a'" in sql


# ---------------------------------------------------------------------------
# Cross-endpoint regression — alice CAN'T reach a Deployment via /api/me/*.
# This guards against future refactors adding cross-tenant accessors.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_endpoints_never_expose_other_tenant_deployments(client, two_tenants):
    """Sanity check: /api/me/* surface returns no deployment/upgrade ids of t-b
    when called as alice (t-a).
    """
    r = await client.get("/api/me/instances", headers=ALICE)
    text = r.text
    assert "dep-b" not in text
    assert "up-b" not in text
    assert "vm-b" not in text


@pytest.mark.asyncio
async def test_unknown_user_gets_tenant_none_and_is_blocked(engine, monkeypatch):
    """Fake-auth caller with X-User: ghost (not in users table) → tenant_id=None.

    tenant_scope() refuses empty tenant_id, so the endpoint fails loudly
    rather than silently returning nothing or leaking. This is the
    documented "fail loud" behavior of auth.get_current_user.

    Uses an isolated client with ASGI exceptions surfacing as HTTP 500 so we
    can assert the status code instead of catching the raw exception.
    """
    import agent_platform_control.db.session as session_mod
    from agent_platform_control.app import create_app
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sm = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine
    session_mod._sessionmaker = sm
    # Seed a tenant + user so the User table exists (ghost will miss this).
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="alice@a.example", display_name="Alice", tenant_id="t-a"))
        await s.commit()

    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/me/instances", headers={"X-User": "ghost"})
        assert r.status_code == 500
    finally:
        session_mod.reset_for_tests()
