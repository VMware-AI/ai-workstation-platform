"""/admin/upgrades API + UpgradeWorker tests.

Coverage targets:
  - plan / start / get happy path + validation
  - double-confirm gate on cutover / rollback / cleanup
  - dry-run cleanup is non-destructive
  - state machine enforcement at the HTTP boundary (409 on illegal edges)
  - worker advances state through the stub vCenter steps
"""

from __future__ import annotations

from datetime import UTC

import pytest
import pytest_asyncio
from agent_platform_control.db.models import AuditView, Tenant, Upgrade, User
from agent_platform_control.orchestrator import upgrade_state as us
from agent_platform_control.orchestrator.upgrade_worker import UpgradeWorker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


@pytest_asyncio.fixture
async def seeded(engine):
    """One tenant + 2 users — enough for a 2-VM upgrade plan."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(
            User(
                id="admin",
                email="admin@example.com",
                display_name="Admin",
                tenant_id="t-a",
                role="admin",
            )
        )
        s.add(User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="t-a"))
        s.add(User(id="bob", email="bob@example.com", display_name="Bob", tenant_id="t-a"))
        await s.commit()
    return sm


def _plan_payload(n: int = 2, dry_run: bool = True) -> dict:
    return {
        "tenant_id": "t-a",
        "from_version": "v0.1.0",
        "to_version": "v0.2.0",
        "dry_run": dry_run,
        "vms": [
            {
                "owner_id": owner,
                "green_vm_id": f"vm-{owner}-green",
                "blue_intended_name": f"vm-{owner}-blue",
            }
            for owner in (["alice", "bob"][:n])
        ],
    }


# ---------- auth ----------


@pytest.mark.asyncio
async def test_plan_requires_admin(client, seeded):
    r = await client.post("/admin/upgrades/plan", json=_plan_payload())
    assert r.status_code == 401

    r = await client.post(
        "/admin/upgrades/plan",
        json=_plan_payload(),
        headers={"X-User": "alice"},
    )
    assert r.status_code == 403


# ---------- plan ----------


@pytest.mark.asyncio
async def test_plan_happy_path(client, seeded):
    r = await client.post("/admin/upgrades/plan", json=_plan_payload(2), headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vm_count"] == 2
    assert body["blue_vm_names"] == ["vm-alice-blue", "vm-bob-blue"]
    assert body["estimated_seconds"] == 600
    assert body["home_volume_strategy"] == "copy"
    assert body["dry_run"] is True


@pytest.mark.asyncio
async def test_plan_rejects_duplicate_blue_names(client, seeded):
    payload = _plan_payload(2)
    payload["vms"][1]["blue_intended_name"] = payload["vms"][0]["blue_intended_name"]
    r = await client.post("/admin/upgrades/plan", json=payload, headers=ADMIN)
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()


# ---------- start ----------


@pytest.mark.asyncio
async def test_start_rejects_dry_run(client, seeded):
    r = await client.post(
        "/admin/upgrades/start", json=_plan_payload(2, dry_run=True), headers=ADMIN
    )
    assert r.status_code == 400
    assert "dry_run" in r.json()["detail"]


@pytest.mark.asyncio
async def test_start_creates_blue_and_green_rows(client, seeded):
    r = await client.post(
        "/admin/upgrades/start",
        json=_plan_payload(2, dry_run=False),
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == us.PLANNED
    assert body["can_rollback"] is True
    assert body["can_cutover"] is False
    assert len(body["vms"]) == 4  # 2 green + 2 blue
    roles = sorted(v["role"] for v in body["vms"])
    assert roles == ["blue", "blue", "green", "green"]
    # Blue rows start without a vm_id; the worker will fill them.
    blue = [v for v in body["vms"] if v["role"] == "blue"]
    assert all(v["vm_id"] is None for v in blue)


# ---------- get ----------


@pytest.mark.asyncio
async def test_get_404(client, seeded):
    r = await client.get("/admin/upgrades/does-not-exist", headers=ADMIN)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_round_trip(client, seeded):
    started = await client.post(
        "/admin/upgrades/start",
        json=_plan_payload(1, dry_run=False),
        headers=ADMIN,
    )
    upgrade_id = started.json()["id"]
    r = await client.get(f"/admin/upgrades/{upgrade_id}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["id"] == upgrade_id


# ---------- double-confirm gate ----------


@pytest.mark.asyncio
async def test_cutover_requires_confirm(client, seeded, engine):
    # Set up an upgrade in blue_ready state directly so we test the gate
    # without running the worker.
    upgrade_id = await _seed_upgrade_at(engine, state=us.BLUE_READY)

    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cutover",
        json={"confirm": False},
        headers=ADMIN,
    )
    assert r.status_code == 409
    assert "confirm=true" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cutover_advances_state(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.BLUE_READY)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cutover",
        json={"confirm": True},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == us.CUTOVER_IN_PROGRESS


@pytest.mark.asyncio
async def test_cutover_rejected_when_not_ready(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.PLANNED)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cutover",
        json={"confirm": True},
        headers=ADMIN,
    )
    assert r.status_code == 409
    assert "blue_ready" in r.json()["detail"]


# ---------- rollback ----------


@pytest.mark.asyncio
async def test_rollback_requires_confirm(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.BLUE_READY)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/rollback",
        json={"confirm": False},
        headers=ADMIN,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_rollback_from_blue_ready(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.BLUE_READY)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/rollback",
        json={"confirm": True},
        headers=ADMIN,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == us.ROLLED_BACK
    assert body["is_terminal"] is True
    # Blue rows should be marked torn_down.
    blue = [v for v in body["vms"] if v["role"] == "blue"]
    assert blue and all(v["status"] == "torn_down" for v in blue)


@pytest.mark.asyncio
async def test_rollback_rejected_after_cutover_done(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.CUTOVER_DONE)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/rollback",
        json={"confirm": True},
        headers=ADMIN,
    )
    assert r.status_code == 409
    assert "cutover" in r.json()["detail"]


# ---------- cleanup ----------


@pytest.mark.asyncio
async def test_cleanup_dry_run_does_not_advance(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.CUTOVER_DONE)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cleanup",
        json={"dry_run": True, "confirm": False},
        headers=ADMIN,
    )
    assert r.status_code == 200
    assert r.json()["state"] == us.CUTOVER_DONE


@pytest.mark.asyncio
async def test_cleanup_real_requires_confirm(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.CUTOVER_DONE)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cleanup",
        json={"dry_run": False, "confirm": False},
        headers=ADMIN,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_cleanup_real_advances_state(client, seeded, engine):
    upgrade_id = await _seed_upgrade_at(engine, state=us.CUTOVER_DONE)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cleanup",
        json={"dry_run": False, "confirm": True},
        headers=ADMIN,
    )
    assert r.status_code == 200
    assert r.json()["state"] == us.CLEANUP_PENDING


# ---------- destructive-op audit trail (doc 34 H-6) ----------


async def _audit_rows(engine) -> list[AuditView]:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        return list((await s.execute(select(AuditView))).scalars().all())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "from_state", "body", "operation"),
    [
        ("cutover", us.BLUE_READY, {"confirm": True}, "upgrade.cutover"),
        ("rollback", us.BLUE_READY, {"confirm": True}, "upgrade.rollback"),
        ("cleanup", us.CUTOVER_DONE, {"dry_run": False, "confirm": True}, "upgrade.cleanup"),
    ],
)
async def test_destructive_upgrade_ops_write_audit_row(
    client, seeded, engine, action: str, from_state: str, body: dict, operation: str
):
    """cutover / rollback / cleanup must land in AuditView, not only the log.

    Regression: _audit() was logger.info-only, so the three destructive
    upgrade operations never appeared in /admin/events ("who cut over the
    upgrade?" was unanswerable from the DB).
    """
    upgrade_id = await _seed_upgrade_at(engine, state=from_state)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/{action}",
        json=body,
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text

    rows = await _audit_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == operation
    assert row.resource == f"upgrade:{upgrade_id}"
    assert row.actor == "admin"
    assert row.result == "success"


@pytest.mark.asyncio
async def test_cleanup_dry_run_writes_no_audit_row(client, seeded, engine):
    """dry_run cleanup is non-destructive — it must not pollute the audit trail."""
    upgrade_id = await _seed_upgrade_at(engine, state=us.CUTOVER_DONE)
    r = await client.post(
        f"/admin/upgrades/{upgrade_id}/cleanup",
        json={"dry_run": True, "confirm": False},
        headers=ADMIN,
    )
    assert r.status_code == 200
    assert await _audit_rows(engine) == []


# ---------- worker ----------


@pytest.mark.asyncio
async def test_worker_advances_to_blue_ready(client, seeded, engine):
    """Worker drives planned -> provisioning_blue -> home_volume_attaching -> blue_ready."""
    started = await client.post(
        "/admin/upgrades/start",
        json=_plan_payload(1, dry_run=False),
        headers=ADMIN,
    )
    upgrade_id = started.json()["id"]

    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = UpgradeWorker(sm, step_delay_s=0.0)

    for _ in range(3):
        n = await worker.drain_once()
        assert n == 1

    # After three ticks the upgrade should be at blue_ready, awaiting cutover.
    r = await client.get(f"/admin/upgrades/{upgrade_id}", headers=ADMIN)
    body = r.json()
    assert body["state"] == us.BLUE_READY
    blue = [v for v in body["vms"] if v["role"] == "blue"]
    assert all(v["vm_id"] is not None for v in blue), "worker should fill blue vm_id"
    assert all(v["status"] == "provisioned" for v in blue)

    # No more drivable rows.
    assert await worker.drain_once() == 0


@pytest.mark.asyncio
async def test_worker_drives_full_lifecycle(client, seeded, engine):
    started = await client.post(
        "/admin/upgrades/start",
        json=_plan_payload(1, dry_run=False),
        headers=ADMIN,
    )
    upgrade_id = started.json()["id"]

    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = UpgradeWorker(sm, step_delay_s=0.0)

    # Worker phase 1: -> blue_ready (3 ticks).
    for _ in range(3):
        await worker.drain_once()

    # Admin cutover (1 user-triggered transition).
    cut = await client.post(
        f"/admin/upgrades/{upgrade_id}/cutover",
        json={"confirm": True},
        headers=ADMIN,
    )
    assert cut.json()["state"] == us.CUTOVER_IN_PROGRESS

    # Worker phase 2: cutover_in_progress -> cutover_done (1 tick).
    await worker.drain_once()
    assert (await client.get(f"/admin/upgrades/{upgrade_id}", headers=ADMIN)).json()[
        "state"
    ] == us.CUTOVER_DONE

    # Admin cleanup.
    cl = await client.post(
        f"/admin/upgrades/{upgrade_id}/cleanup",
        json={"dry_run": False, "confirm": True},
        headers=ADMIN,
    )
    assert cl.json()["state"] == us.CLEANUP_PENDING

    # Worker phase 3: cleanup_pending -> completed.
    await worker.drain_once()
    final = (await client.get(f"/admin/upgrades/{upgrade_id}", headers=ADMIN)).json()
    assert final["state"] == us.COMPLETED
    assert final["is_terminal"] is True


@pytest.mark.asyncio
async def test_worker_skips_when_no_drivable_rows(engine, seeded):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = UpgradeWorker(sm, step_delay_s=0.0)
    assert await worker.drain_once() == 0


@pytest.mark.asyncio
async def test_worker_marks_failed_on_exception(engine, seeded, monkeypatch):
    """If a stub step raises, the upgrade lands in 'failed' with reason."""
    upgrade_id = await _seed_upgrade_at(engine, state=us.PLANNED)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = UpgradeWorker(sm, step_delay_s=0.0)

    async def boom(_self, _up):
        raise RuntimeError("vCenter unreachable")

    monkeypatch.setattr(UpgradeWorker, "_do_plan", boom)

    await worker.drain_once()

    async with sm() as s:
        up = await s.get(Upgrade, upgrade_id)
        assert up is not None
        assert up.state == us.FAILED
        assert up.failure_reason is not None
        assert "vCenter unreachable" in up.failure_reason


# ---------- helpers ----------


async def _seed_upgrade_at(engine, *, state: str) -> str:
    """Insert one Upgrade row at the given state with one blue + one green VM."""
    import uuid
    from datetime import datetime

    from agent_platform_control.db.models import UpgradeVM

    sm = async_sessionmaker(engine, expire_on_commit=False)
    upgrade_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(
            Upgrade(
                id=upgrade_id,
                tenant_id="t-a",
                from_version="v0.1.0",
                to_version="v0.2.0",
                state=state,
                started_by="admin",
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            UpgradeVM(
                upgrade_id=upgrade_id,
                role="green",
                vm_id="vm-alice-green",
                intended_name="vm-alice-green",
                owner_id="alice",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            UpgradeVM(
                upgrade_id=upgrade_id,
                role="blue",
                vm_id=None,
                intended_name="vm-alice-blue",
                owner_id="alice",
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return upgrade_id
