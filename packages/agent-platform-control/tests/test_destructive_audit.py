"""Harness doc 34 H-6/H-7 — destructive ops write an audit row + sanitize text.

Before this, cleanup_cron auto-destroyed VMs after 24h with only a logger.info
line and zero audit trail ("who destroyed VM X?" was unanswerable), and vCenter
error strings were logged/returned verbatim (log/prompt-injection + bloat).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_platform_control.db.models import (
    AuditView,
    Deployment,
    DeploymentItem,
    Tenant,
    User,
)
from agent_platform_control.orchestrator.audit import record_audit, sanitize
from agent_platform_control.orchestrator.cleanup_cron import cleanup_failed_vms
from agent_platform_control.orchestrator.fake import FakeProvisioner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---- sanitize ----


def test_sanitize_strips_control_chars():
    raw = "vCenter said\x00 bad\x07\x1b[31m thing\x9f"
    out = sanitize(raw)
    assert "\x00" not in out and "\x07" not in out and "\x9f" not in out
    assert "vCenter said bad" in out.replace("\x1b", "")  # ESC also stripped


def test_sanitize_truncates_long_text():
    out = sanitize("x" * 5000, limit=500)
    assert len(out) <= 500 + len("…(truncated)")
    assert out.endswith("…(truncated)")


def test_sanitize_keeps_newlines_and_tabs():
    out = sanitize("line1\nline2\tcol")
    assert out == "line1\nline2\tcol"


# ---- cleanup_cron audit ----


@pytest_asyncio.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_stale_failed_vm(sm) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=48)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="t",
                image_version="v0.1.0",
                state="failed",
                requested_count=1,
                failed_count=1,
            )
        )
        s.add(
            DeploymentItem(
                id=1,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="vm-a",
                user_token_enc="e",
                user_token_hash="h",
                vm_id="vsphere-vm-001",
                state="failed",
                updated_at=long_ago,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_cleanup_cron_records_audit_for_each_destroy(sm):
    await _seed_stale_failed_vm(sm)
    n = await cleanup_failed_vms(sm, FakeProvisioner(), retain_hours=24)
    assert n == 1

    async with sm() as s:
        rows = (await s.execute(select(AuditView))).scalars().all()
    destroys = [r for r in rows if r.operation == "vm.destroy"]
    assert len(destroys) == 1, "each auto-destroy must leave an audit row"
    a = destroys[0]
    assert a.resource == "vsphere-vm-001"
    assert a.result == "success"
    assert "cron" in a.actor  # automated actor is identifiable
    assert a.params and a.params.get("deployment_item_id") == 1


@pytest.mark.asyncio
async def test_record_audit_is_committed_with_caller_txn(sm):
    async with sm() as s:
        record_audit(
            s,
            actor="test",
            operation="vm.destroy",
            resource="vm-x",
            params={"k": "v"},
            result="success",
        )
        await s.commit()
    async with sm() as s:
        rows = (await s.execute(select(AuditView))).scalars().all()
    assert len(rows) == 1 and rows[0].resource == "vm-x"
