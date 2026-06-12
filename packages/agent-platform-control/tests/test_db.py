"""Verify the 6 tables exist and basic CRUD works."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agent_platform_control.db.models import (
    Deployment,
    DeploymentItem,
    Tenant,
    TokenUsage,
    User,
)
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.mark.asyncio
async def test_all_six_tables_created(engine):
    def _names(sync_conn):
        return set(inspect(sync_conn).get_table_names())

    async with engine.connect() as conn:
        names = await conn.run_sync(_names)
    expected = {"tenants", "users", "image_versions", "vms", "token_usage", "audit_view"}
    assert expected.issubset(names), f"missing: {expected - names}"


@pytest.mark.asyncio
async def test_create_tenant_and_user(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="acme", display_name="Acme Corp"))
        await s.commit()
        s.add(User(id="alice", email="alice@acme.test", display_name="Alice", tenant_id="acme"))
        await s.commit()

        u = (await s.execute(select(User).where(User.id == "alice"))).scalar_one()
        assert u.email == "alice@acme.test"
        assert u.role == "user"
        assert u.tenant_id == "acme"
        # Decision 4 PR-C: ssh_pubkey is nullable and defaults to None
        assert u.ssh_pubkey is None


@pytest.mark.asyncio
async def test_user_ssh_pubkey_roundtrip(engine):
    """Decision 4 PR-C: portal-uploaded SSH key lives on User."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample alice@laptop"
    async with sm() as s:
        s.add(Tenant(id="acme", display_name="Acme"))
        s.add(
            User(
                id="alice",
                email="alice@acme.test",
                display_name="Alice",
                tenant_id="acme",
                ssh_pubkey=pubkey,
            )
        )
        await s.commit()

        u = (await s.execute(select(User).where(User.id == "alice"))).scalar_one()
        assert u.ssh_pubkey == pubkey


@pytest.mark.asyncio
async def test_token_usage_insert(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="acme", display_name="Acme"))
        s.add(User(id="alice", email="alice@acme.test", display_name="Alice", tenant_id="acme"))
        await s.commit()

        s.add(
            TokenUsage(
                user_id="alice",
                tenant_id="acme",
                agent="qcoder",
                model="qwen-coder-32b",
                in_tokens=100,
                out_tokens=200,
                duration_s=1.5,
                ts=datetime.now(UTC),
            )
        )
        await s.commit()

        rows = (await s.execute(select(TokenUsage))).scalars().all()
        assert len(rows) == 1
        assert rows[0].agent == "qcoder"
        assert rows[0].in_tokens + rows[0].out_tokens == 300


@pytest.mark.asyncio
async def test_deployment_item_token_issued_at_defaults_null_and_roundtrips(engine):
    """Decision 8 PR-D: token_issued_at is the TTL clock start.

    Existing rows from before this migration default to NULL ("no clock
    started") so they keep working. New rows can be stamped with the
    extraConfig-injection timestamp.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(Tenant(id="acme", display_name="Acme"))
        s.add(User(id="alice", email="alice@acme.test", display_name="Alice", tenant_id="acme"))
        await s.commit()
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="acme",
                requested_by="alice",
                template="tpl",
                image_version="v0.1.0",
                state="pending",
                requested_count=2,
            )
        )
        await s.commit()
        # One item without token_issued_at (pre-migration semantic)
        s.add(
            DeploymentItem(
                id=1,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="alice-vm-a",
                user_token_enc="enc-a",
                user_token_hash="hash-a",
            )
        )
        # One item with the clock started
        s.add(
            DeploymentItem(
                id=2,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="alice-vm-b",
                user_token_enc="enc-b",
                user_token_hash="hash-b",
                token_issued_at=now,
            )
        )
        await s.commit()

        items = {i.id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
        assert items[1].token_issued_at is None
        assert items[2].token_issued_at is not None
        # SQLite drops tzinfo on roundtrip; compare wall-clock components only.
        round_tripped = items[2].token_issued_at
        assert round_tripped.year == now.year
        assert round_tripped.month == now.month
        assert round_tripped.day == now.day
        assert round_tripped.hour == now.hour
        assert round_tripped.minute == now.minute
