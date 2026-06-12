"""E2E software smoke cassette (PR-H H-4).

Mirrors the ``docs/architecture/acceptance/m1.md`` A1-A5 flow but runs
purely in-process (ASGITransport + FakeProvisioner + DeploymentWorker
.drain_once()), no docker / vcsim / network required. Designed to
finish in < 5 seconds so CI can run it on every PR.

Coverage matrix vs the M1 acceptance:
  A1 — submit deployment (admin path) ............... ✅
  A2 — approval batch (POST /api/approvals/{id}/deploy)
       ... DEFERRED until #129 from-approval merges
  A3 — worker drains → state=succeeded .............. ✅
  A4 — heartbeat ingest (POST /api/heartbeat)
       ... DEFERRED until #136 (PR-D) heartbeat endpoint merges
  A5 — admin re-reads deployment ..................... ✅
  R1/R2 — heartbeat sweepers
       ... DEFERRED until #136 cron classes merge
  R3 — failed-cleanup cron
       ... DEFERRED until #136 cleanup_cron merges
  R4/R5 — runtime memory/uptime ..... NOT IN LOCAL HARNESS (prod-only)

When the deferred PRs merge, the @pytest.mark.skip blocks below
become @pytest.mark.asyncio + a real assertion. Each skip has a
TODO marker keyed to its blocking PR so grep finds them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from agent_platform_control.api.cloud_init import get_vm_secret_store
from agent_platform_control.db.models import VM, DeploymentItem
from agent_platform_control.orchestrator.cleanup_cron import (
    cleanup_failed_vms,
    heartbeat_sweep,
)
from agent_platform_control.orchestrator.fake import FakeProvisioner
from agent_platform_control.orchestrator.worker import DeploymentWorker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .lib_smoke import (
    ADMIN_HEADERS,  # noqa: F401  (re-exported for downstream demos)
    DEFAULT_USER_ID,
    fetch_deployment,
    submit_deployment,
)

# smoke_client + smoke_seed fixtures come from conftest.py at this dir level.

# A real ed25519 public key so the SSH-pubkey upload path validates + flows
# through provisioning end-to-end (#350). The matching private key is not used.
SMOKE_SSH_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIzo5L45TVm+7zwuSYQCPyYZn5K478jF3iyB6SHVRIrx smoke@350"
)


pytestmark = pytest.mark.asyncio


def _build_test_worker(sm) -> DeploymentWorker:
    """Construct a DeploymentWorker with stub URLs for the cassette.

    Smoke is in-process: the URLs are baked into VM CloneSpec metadata
    and never dialled. Anything reachable-looking is fine.

    The worker shares the process-wide VmSecretStore the cloud-init exchange
    endpoint reads from (#350), so a provisioned secret is redeemable in the
    same test.
    """
    return DeploymentWorker(
        sm,
        FakeProvisioner(),
        registry_url="registry.invalid/smoke",
        goose_image_tag="0.0.0-smoke",
        litellm_gateway_url="http://gateway.invalid",
        heartbeat_url="http://control.invalid/heartbeat",
        secret_store=get_vm_secret_store(),
    )


async def _drain(worker: DeploymentWorker, *, ticks: int = 10) -> None:
    """Drive the worker until idle or ``ticks`` exhausted."""
    for _ in range(ticks):
        if await worker.drain_once() == 0:
            break


async def _exchange_token(client, user_token: str) -> dict:
    """Redeem a bootstrap token via POST /api/cloud-init/exchange-token."""
    resp = await client.post(
        "/api/cloud-init/exchange-token",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 200, f"exchange-token → {resp.status_code}: {resp.text}"
    return resp.json()


async def test_a1_submit_succeeds(smoke_client):
    """M1 A1: deployment submission returns 201 + pending state."""
    body = await submit_deployment(smoke_client, count=1)
    assert body["state"] == "pending", (
        f"expected state=pending, got {body['state']!r} — worker may have already run"
    )
    assert body["counts"]["requested"] == 1


async def test_a1_a3_a5_happy_path(smoke_client, engine):
    """M1 happy path (A1 → A3 → A5):

    1. Submit deployment (A1)
    2. Run DeploymentWorker.drain_once until items succeed (A3)
    3. Re-read deployment, assert succeeded counts + items[0].vm_id (A5)
    """
    # A1 — submit
    body = await submit_deployment(smoke_client, count=2)
    deployment_id = body["id"]
    assert all(it["state"] == "pending" for it in body["items"])

    # A3 — drain the worker until both items reach a terminal state.
    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = _build_test_worker(sm)
    drained = 0
    for _ in range(10):  # 10 ticks is plenty for 2 items
        processed = await worker.drain_once()
        drained += processed
        if drained >= 2:
            break
    assert drained == 2, (
        f"A3 FAILED — worker drained {drained}/2 items in 10 ticks. "
        "fix: check that DeploymentItem rows actually have state=pending."
    )

    # A5 — admin re-reads. State vocabulary on origin/main:
    # deployment.state ∈ pending|in_progress|completed|partial|failed
    # item.state ∈ pending|cloning|powered_on|failed
    final = await fetch_deployment(smoke_client, deployment_id)
    assert final["state"] == "completed", (
        f"A5 FAILED — deployment state={final['state']!r} after worker drain.\n"
        f"items: {final['items']}"
    )
    assert final["counts"]["succeeded"] == 2
    assert final["counts"]["failed"] == 0
    for item in final["items"]:
        assert item["state"] == "powered_on", item
        assert item["vm_id"], f"A5 FAILED — item {item['id']} succeeded but vm_id is empty"


async def test_a3_partial_failure_surfaces_in_state(smoke_client, engine):
    """Resilience smoke: a failed item must propagate to deployment state."""
    body = await submit_deployment(smoke_client, count=2)
    deployment_id = body["id"]
    assert deployment_id  # silence ruff; deployment_id read below as well

    # FakeProvisioner that permanently fails the first item.
    # Note: the worker retries failures, so an attempt-counter predicate
    # would eventually succeed; we key on intended_name suffix instead so
    # item 001 stays failed across every retry.
    sm = async_sessionmaker(engine, expire_on_commit=False)

    def fail_one(spec):
        return "smoke: forced failure" if spec.intended_name.endswith("001") else None

    worker = DeploymentWorker(
        sm,
        FakeProvisioner(fail_when=fail_one),
        registry_url="registry.invalid/smoke",
        goose_image_tag="0.0.0-smoke",
        litellm_gateway_url="http://gateway.invalid",
        heartbeat_url="http://control.invalid/heartbeat",
    )
    # Drain until both items reach a terminal state. Worker may retry
    # failed items several times before the deployment closes — give it
    # 30 ticks to settle so we don't false-fail on slow retry attempts.
    for _ in range(30):
        processed = await worker.drain_once()
        if processed == 0:
            break

    final = await fetch_deployment(smoke_client, deployment_id)
    assert final["counts"]["failed"] == 1, final["counts"]
    assert final["counts"]["succeeded"] == 1, final["counts"]
    assert final["state"] in ("partially_failed", "failed"), final["state"]


async def test_a3_writes_token_columns(smoke_client, engine):
    """Smoke regression: PR-A's user_token_enc / hash columns get populated.

    Catches the failure mode where a worker happy path leaves tokens NULL
    despite vm_id being set (would silently break heartbeat lookup).
    """
    await submit_deployment(smoke_client, count=1)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    worker = _build_test_worker(sm)
    for _ in range(5):
        if await worker.drain_once() == 0:
            break

    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalars().first()
        assert item is not None
        assert item.user_token_enc, "user_token_enc must be populated"
        assert item.user_token_hash, "user_token_hash must be populated"
        assert item.state == "powered_on"


# ----------------------------------------------------------------- DEFERRED


@pytest.mark.skip(reason="TODO PR #129 from-approval — A2 approval-driven path")
async def test_a2_approval_triggers_deployment():
    """When #129 merges, exercise POST /api/approvals/{id}/deploy."""


# ------------------------------------------------- A4 + R1/R2/R3 (wired in #350)


async def test_a4_heartbeat_updates_last_seen(smoke_client, engine):
    """A4 (#350): clone → exchange-token yields a non-empty heartbeat token →
    POST /api/heartbeat returns 200 → vms.last_heartbeat_at is stamped.

    Exercises the full供给侧: worker provisions the per-VM heartbeat secret +
    stamps DeploymentItem.heartbeat_token_hash, cloud-init redeems it, the
    in-VM agent then authenticates against /api/heartbeat.
    """
    # The owner uploads an SSH pubkey first so we also assert it flows through.
    put = await smoke_client.put(
        "/api/me/ssh-pubkey",
        json={"pubkey": SMOKE_SSH_PUBKEY},
        headers={"X-User": DEFAULT_USER_ID},
    )
    assert put.status_code == 200, put.text

    body = await submit_deployment(smoke_client, count=1)
    deployment_id = body["id"]

    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = _build_test_worker(sm)
    await _drain(worker)

    final = await fetch_deployment(smoke_client, deployment_id)
    assert final["counts"]["succeeded"] == 1, final["counts"]

    # The worker must have stamped a heartbeat hash on the (now powered_on) item.
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalars().one()
        assert item.heartbeat_token_hash, "worker did not stamp heartbeat_token_hash"
        # Recover the plaintext bootstrap token the VM would receive via OVF.
        from agent_platform_control.orchestrator.tokens import decrypt_user_token

        user_token = decrypt_user_token(item.user_token_enc)
        vm_id = item.vm_id

    # cloud-init redeems the bootstrap token → gets the per-VM secrets payload.
    payload = await _exchange_token(smoke_client, user_token)
    heartbeat_token = payload["secrets"]["heartbeat_access_token"]
    assert heartbeat_token, "exchange-token returned an empty heartbeat token"
    assert payload["secrets"]["ssh_authorized_keys"] == SMOKE_SSH_PUBKEY

    # in-VM agent → POST /api/heartbeat with the redeemed token.
    hb = await smoke_client.post(
        "/api/heartbeat",
        headers={"Authorization": f"Bearer {heartbeat_token}"},
    )
    assert hb.status_code == 200, hb.text
    assert hb.json()["vm_id"] == vm_id

    async with sm() as s:
        vm = await s.get(VM, vm_id)
        assert vm is not None
        assert vm.last_heartbeat_at is not None, "heartbeat did not stamp last_heartbeat_at"


async def _powered_on_vm_with_heartbeat(smoke_client, engine, *, last_seen: datetime) -> str:
    """Clone one VM, send a heartbeat, then backdate last_heartbeat_at.

    Returns the vm_id. Used by R1/R2 to put a VM in a known "last seen N ago"
    state without sleeping.
    """
    body = await submit_deployment(smoke_client, count=1)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    worker = _build_test_worker(sm)
    await _drain(worker)

    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalars().one()
        vm_id = item.vm_id
        vm = await s.get(VM, vm_id)
        vm.state = "running"
        vm.last_heartbeat_at = last_seen
        await s.commit()
    assert body  # silence ruff
    return vm_id


async def test_r1_unhealthy_after_5_min_no_heartbeat(smoke_client, engine):
    """R1 (#350): a VM last seen >5min (but <30min) ago → unhealthy on sweep."""
    six_min_ago = datetime.now(UTC) - timedelta(minutes=6)
    vm_id = await _powered_on_vm_with_heartbeat(smoke_client, engine, last_seen=six_min_ago)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    unhealthy, lost = await heartbeat_sweep(sm)
    assert (unhealthy, lost) == (1, 0)

    async with sm() as s:
        vm = await s.get(VM, vm_id)
        assert vm.state == "unhealthy"


async def test_r2_lost_after_30_min_no_heartbeat(smoke_client, engine):
    """R2 (#350): a VM last seen >30min ago → lost on sweep."""
    forty_min_ago = datetime.now(UTC) - timedelta(minutes=40)
    vm_id = await _powered_on_vm_with_heartbeat(smoke_client, engine, last_seen=forty_min_ago)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    unhealthy, lost = await heartbeat_sweep(sm)
    assert (unhealthy, lost) == (0, 1)

    async with sm() as s:
        vm = await s.get(VM, vm_id)
        assert vm.state == "lost"


async def test_r3_failed_deployment_cleaned_after_24h(smoke_client, engine):
    """R3 (#350): a failed item past the retain window → destroyed + cleaned."""
    body = await submit_deployment(smoke_client, count=1)
    assert body
    sm = async_sessionmaker(engine, expire_on_commit=False)

    # Force the clone to fail so the item lands in 'failed' with a vm_id —
    # the cleanup cron only reaps failed items bound to a VM.
    fake = FakeProvisioner(fail_when=lambda spec: "smoke: forced failure")
    worker = DeploymentWorker(
        sm,
        fake,
        registry_url="registry.invalid/smoke",
        goose_image_tag="0.0.0-smoke",
        litellm_gateway_url="http://gateway.invalid",
        heartbeat_url="http://control.invalid/heartbeat",
        secret_store=get_vm_secret_store(),
    )
    for _ in range(10):
        if await worker.drain_once() == 0:
            break

    # FakeProvisioner failure leaves no vm_id, so seed one + backdate updated_at
    # past the 24h retain window to make the item cleanup-eligible.
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalars().one()
        assert item.state == "failed"
        item.vm_id = "vm-smoke-failed"
        item.updated_at = datetime.now(UTC) - timedelta(hours=25)
        await s.commit()

    cleaned = await cleanup_failed_vms(sm, fake, retain_hours=24)
    assert cleaned == 1

    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalars().one()
        assert item.state == "cleaned"
