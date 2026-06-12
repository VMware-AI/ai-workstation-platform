"""/api/ingest/token-usage — service-side TokenUsage writer.

Covers acceptance 1.5.4 ("一次调用对应一行") + service-token auth boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agent_platform_control.db.models import TokenUsage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

SERVICE = {"Authorization": "Bearer dev-ingest-token-CHANGE-ME"}
ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


def _valid_event() -> dict:
    return {
        "user_id": "alice",
        "tenant_id": "t-a",
        "agent": "claude-code",
        "model": "qwen-coder-32b",
        "in_tokens": 120,
        "out_tokens": 45,
        "duration_s": 1.23,
    }


@pytest.mark.asyncio
async def test_ingest_writes_row(client, engine):
    resp = await client.post("/api/ingest/token-usage", json=_valid_event(), headers=SERVICE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert isinstance(body["id"], int) and body["id"] > 0

    # Confirm one row exists with the expected values
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        rows = (await s.execute(select(TokenUsage))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == "alice"
    assert row.tenant_id == "t-a"
    assert row.agent == "claude-code"
    assert row.model == "qwen-coder-32b"
    assert row.in_tokens == 120
    assert row.out_tokens == 45
    assert row.duration_s == pytest.approx(1.23)
    assert row.ts is not None


@pytest.mark.asyncio
async def test_ingest_one_post_one_row(client, engine):
    # Three POSTs → three rows (1.5.4 acceptance: "一次调用对应一行")
    for i in range(3):
        ev = _valid_event() | {"in_tokens": 10 * i}
        resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
        assert resp.status_code == 201

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        count = len((await s.execute(select(TokenUsage))).scalars().all())
    assert count == 3


@pytest.mark.asyncio
async def test_ingest_requires_bearer(client):
    resp = await client.post("/api/ingest/token-usage", json=_valid_event())
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingest_rejects_admin_token(client):
    # Admin token must NOT work — service token is separate authority
    resp = await client.post("/api/ingest/token-usage", json=_valid_event(), headers=ADMIN)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_rejects_unknown_agent(client):
    ev = _valid_event() | {"agent": "evil-agent"}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 422
    assert "agent must be one of" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_accepts_xiaoguai(client, engine):
    # 小怪 is the platform's preferred default agent (adapters/xiaoguai.py); its
    # token calls must not be rejected at ingest. Regression for the agent-kind gap.
    ev = _valid_event() | {"agent": "xiaoguai"}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 201, resp.text
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        row = (await s.execute(select(TokenUsage))).scalars().one()
    assert row.agent == "xiaoguai"


@pytest.mark.asyncio
async def test_ingest_rejects_negative_tokens(client):
    ev = _valid_event() | {"in_tokens": -1}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_persists_correlation_id(client, engine):
    # G1 (doc 37 §4.4): correlation-id threads agent → 网关 → 计量 for full-chain trace.
    ev = _valid_event() | {"correlation_id": "corr-abc-123"}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 201, resp.text

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        row = (await s.execute(select(TokenUsage))).scalars().one()
    assert row.correlation_id == "corr-abc-123"


@pytest.mark.asyncio
async def test_ingest_accepts_long_correlation_id(client, engine):
    # C-1 review fix: the gateway's _SANE_REQUEST_ID allows up to 128 chars, so a
    # 65–128-char x-request-id must NOT be 422'd at ingest (that dropped the whole
    # token_usage event = silent metering loss). Align ingest to 128.
    cid = "req-" + "a" * 124  # 128 chars, all in [A-Za-z0-9._-]
    assert len(cid) == 128
    ev = _valid_event() | {"correlation_id": cid}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 201, resp.text
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        row = (await s.execute(select(TokenUsage))).scalars().one()
    assert row.correlation_id == cid


@pytest.mark.asyncio
async def test_ingest_rejects_control_char_correlation_id(client):
    # Mi-1 review fix: ingest must not trust the gateway's charset validation —
    # re-reject non-[A-Za-z0-9._-] so a control char can't reach the JSON sink.
    ev = _valid_event() | {"correlation_id": "bad\ninjection"}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_correlation_id_optional(client, engine):
    # Backward-compatible: events without correlation_id still succeed (nullable).
    resp = await client.post("/api/ingest/token-usage", json=_valid_event(), headers=SERVICE)
    assert resp.status_code == 201, resp.text
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        row = (await s.execute(select(TokenUsage))).scalars().one()
    assert row.correlation_id is None


@pytest.mark.asyncio
async def test_ingest_honors_client_ts(client, engine):
    ts = datetime(2026, 5, 24, 6, 0, 0, tzinfo=UTC)
    ev = _valid_event() | {"ts": ts.isoformat()}
    resp = await client.post("/api/ingest/token-usage", json=ev, headers=SERVICE)
    assert resp.status_code == 201

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        row = (await s.execute(select(TokenUsage))).scalars().one()
    # Strip tzinfo for SQLite portability; just check the wall-clock match
    assert row.ts.replace(tzinfo=UTC) == ts
