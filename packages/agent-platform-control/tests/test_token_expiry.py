"""Tests for orchestrator.token_expiry — bootstrap-token TTL sweeper (PR-D D-3).

Decision 8 (locked 2026-05-28): 30-minute TTL from extraConfig injection.
Sweeper marks expired items failed, increments deployment failed_count,
and recomputes deployment aggregate state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import Deployment, DeploymentItem, Tenant, User
from agent_platform_control.orchestrator.token_expiry import (
    DEFAULT_TTL_SECONDS,
    sweep_expired_tokens,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture
async def seeded(engine):
    """One deployment with 4 items in mixed TTL states."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    long_ago = now - timedelta(minutes=45)  # expired
    just_now = now - timedelta(minutes=5)  # not yet
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="tpl",
                image_version="v0.1.0",
                state="running",
                requested_count=4,
            )
        )
        # 1: expired + not consumed + state powered_on → should be flipped
        s.add(
            DeploymentItem(
                id=1,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-a",
                user_token_enc="e1",
                user_token_hash="h1",
                token_issued_at=long_ago,
                state="powered_on",
            )
        )
        # 2: fresh + not consumed → keep
        s.add(
            DeploymentItem(
                id=2,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-b",
                user_token_enc="e2",
                user_token_hash="h2",
                token_issued_at=just_now,
                state="powered_on",
            )
        )
        # 3: expired BUT consumed (legitimate boot) → keep
        s.add(
            DeploymentItem(
                id=3,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-c",
                user_token_enc="e3",
                user_token_hash="h3",
                token_issued_at=long_ago,
                token_consumed_at=now,
                state="powered_on",
            )
        )
        # 4: no token_issued_at (pre-PR-D row) → keep, no clock started
        s.add(
            DeploymentItem(
                id=4,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-d",
                user_token_enc="e4",
                user_token_hash="h4",
                state="powered_on",
            )
        )
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_default_ttl_is_30_minutes():
    assert DEFAULT_TTL_SECONDS == 30 * 60


@pytest.mark.asyncio
async def test_sweep_marks_expired_unconsumed_failed(seeded):
    n = await sweep_expired_tokens(seeded)
    assert n == 1
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[1].state == "failed"
    assert "expired" in (items[1].error_message or "").lower()


@pytest.mark.asyncio
async def test_sweep_skips_fresh_token(seeded):
    await sweep_expired_tokens(seeded)
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[2].state == "powered_on"


@pytest.mark.asyncio
async def test_sweep_skips_consumed_token(seeded):
    await sweep_expired_tokens(seeded)
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[3].state == "powered_on"
    assert items[3].error_message is None


@pytest.mark.asyncio
async def test_sweep_skips_unissued_token(seeded):
    """Pre-PR-D rows have token_issued_at=NULL = no clock started → never expires."""
    await sweep_expired_tokens(seeded)
    async with seeded() as s:
        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
    assert items[4].state == "powered_on"


@pytest.mark.asyncio
async def test_sweep_increments_deployment_failed_count(seeded):
    await sweep_expired_tokens(seeded)
    async with seeded() as s:
        dep = await s.get(Deployment, "dep-1")
    assert dep.failed_count == 1
    # 1/4 failed → deployment still running (3 remain in flight)
    assert dep.state == "running"


@pytest.mark.asyncio
async def test_sweep_is_idempotent(seeded):
    """Running twice marks the same items only once."""
    first = await sweep_expired_tokens(seeded)
    second = await sweep_expired_tokens(seeded)
    assert first == 1
    assert second == 0
    async with seeded() as s:
        dep = await s.get(Deployment, "dep-1")
    assert dep.failed_count == 1


@pytest.mark.asyncio
async def test_sweep_counts_cancelled_items_toward_terminal(engine):
    """TTL expiry on the last in-flight item must terminalize a deployment
    whose other items were cancelled.

    Regression: the sweeper hand-rolled its aggregate (failed + succeeded
    only, ignoring cancelled_count) instead of calling
    deployment_state.recompute_deployment_state — a deployment with one
    cancelled item + one expired item stayed 'running' forever.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    long_ago = now - timedelta(minutes=45)
    async with sm() as s:
        s.add(Tenant(id="t-b", display_name="B"))
        s.add(User(id="bob", email="b@x", display_name="B", tenant_id="t-b"))
        s.add(
            Deployment(
                id="dep-2",
                tenant_id="t-b",
                requested_by="bob",
                template="tpl",
                image_version="v0.1.0",
                state="running",
                requested_count=2,
                cancelled_count=1,
            )
        )
        # expired + unconsumed → sweep flips to failed
        s.add(
            DeploymentItem(
                deployment_id="dep-2",
                owner_id="bob",
                intended_name="vm-e",
                user_token_enc="e10",
                user_token_hash="h10",
                token_issued_at=long_ago,
                state="powered_on",
            )
        )
        # already cancelled by the admin before its clone started
        s.add(
            DeploymentItem(
                deployment_id="dep-2",
                owner_id="bob",
                intended_name="vm-f",
                user_token_enc="e11",
                user_token_hash="h11",
                state="cancelled",
            )
        )
        await s.commit()

    n = await sweep_expired_tokens(sm)
    assert n == 1
    async with sm() as s:
        dep = await s.get(Deployment, "dep-2")
    assert dep.failed_count == 1
    # 0 succeeded + 1 failed + 1 cancelled = all 2 accounted → terminal
    assert dep.state == "failed"


@pytest.mark.asyncio
async def test_sweep_recomputes_completed_when_all_terminal(seeded):
    """When the sweep brings the deployment to fully-terminal, state aggregates."""
    # Pre-mark the other 3 items as succeeded so this one expiry tips it over.
    async with seeded() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        for i in items:
            if i.id != 1:
                i.token_consumed_at = datetime.now(UTC)
                i.state = "ready"
        dep = await s.get(Deployment, "dep-1")
        dep.succeeded_count = 3
        await s.commit()

    await sweep_expired_tokens(seeded)
    async with seeded() as s:
        dep = await s.get(Deployment, "dep-1")
    # 3 success + 1 fail = partially_failed
    assert dep.state == "partially_failed"
    assert dep.failed_count == 1
