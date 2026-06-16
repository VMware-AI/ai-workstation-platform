"""DeploymentWorker integration tests using FakeProvisioner."""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_control.db.models import VM, Deployment, DeploymentItem, Tenant, User
from agent_platform_control.orchestrator import DeploymentWorker
from agent_platform_control.orchestrator.agent_user import (
    UID_MAX,
    UID_MIN,
    derive_uid,
    sanitize_username,
)
from agent_platform_control.orchestrator.fake import FakeProvisioner
from agent_platform_control.orchestrator.tokens import encrypt_user_token, hash_user_token
from agent_platform_control.orchestrator.worker import MAX_ATTEMPTS, educational_error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

WORKER_ARGS = {
    "registry_url": "registry.test/agent-platform",
    "goose_image_tag": "1.34.1",
    "litellm_gateway_url": "http://gw.test:4000",
    "heartbeat_url": "http://ctl.test/v1/heartbeat",
    "poll_interval_s": 0.05,
}


def _token_fields(token: str) -> dict[str, str]:
    return {
        "user_token_enc": encrypt_user_token(token),
        "user_token_hash": hash_user_token(token),
    }


@pytest_asyncio.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def seeded_batch(sm):
    """Create a batch with 3 pending items + their tenant + users."""
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(User(id="bob", email="b@x", display_name="B", tenant_id="t-a"))
        s.add(User(id="carol", email="c@x", display_name="C", tenant_id="t-a"))
        s.add(User(id="admin-1", email="op@x", display_name="Op", tenant_id="t-a", role="admin"))
        dep = Deployment(
            id="dep-1",
            tenant_id="t-a",
            requested_by="admin-1",
            template="[t] agent-platform.vmtx",
            image_version="v0.1.0",
            state="pending",
            requested_count=3,
        )
        s.add(dep)
        for owner in ("alice", "bob", "carol"):
            s.add(
                DeploymentItem(
                    deployment_id="dep-1",
                    owner_id=owner,
                    intended_name=f"vm-{owner}",
                    **_token_fields(f"tok_{owner}"),
                )
            )
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_drain_succeeds_all(seeded_batch):
    sm = seeded_batch
    worker = DeploymentWorker(sm, FakeProvisioner(), **WORKER_ARGS)

    processed = 0
    for _ in range(10):
        processed += await worker.drain_once()
    assert processed == 3

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "completed"
        assert dep.succeeded_count == 3
        assert dep.failed_count == 0

        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert {i.state for i in items} == {"powered_on"}

        vms = (await s.execute(select(VM))).scalars().all()
        assert len(vms) == 3
        assert {v.state for v in vms} == {"running"}


@pytest.mark.asyncio
async def test_drain_partial_failure(seeded_batch):
    sm = seeded_batch
    # Fail when intended_name targets carol.
    fake = FakeProvisioner(fail_when=lambda spec: "boom" if spec.owner_id == "carol" else None)
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)

    for _ in range(10):
        await worker.drain_once()

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "partially_failed"
        assert dep.succeeded_count == 2
        assert dep.failed_count == 1

        items = {i.owner_id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
        assert items["alice"].state == "powered_on"
        assert items["bob"].state == "powered_on"
        assert items["carol"].state == "failed"
        # Substring match — error_message is now wrapped with an educational
        # hint after retry-once (Task 1.11.4). Raw still appears verbatim.
        assert "boom" in items["carol"].error_message


@pytest.mark.asyncio
async def test_drain_all_fail(seeded_batch):
    sm = seeded_batch
    fake = FakeProvisioner(fail_when=lambda _spec: "nope")
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    for _ in range(10):
        await worker.drain_once()

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "failed"
        assert dep.failed_count == 3


@pytest.mark.asyncio
async def test_drain_idle_returns_zero(sm):
    worker = DeploymentWorker(sm, FakeProvisioner(), **WORKER_ARGS)
    assert await worker.drain_once() == 0


@pytest.mark.asyncio
async def test_provisioner_exception_marks_failed(seeded_batch):
    """If the provisioner raises, the worker must still mark the item failed,
    not leave it stuck in 'cloning' forever after one attempt."""
    sm = seeded_batch

    class BoomProvisioner:
        async def clone_vm(self, _spec):
            raise RuntimeError("connection refused")

    worker = DeploymentWorker(sm, BoomProvisioner(), **WORKER_ARGS)
    for _ in range(10):
        await worker.drain_once()

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "failed"
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert all(i.state == "failed" for i in items)
        assert all("connection refused" in (i.error_message or "") for i in items)


@pytest.mark.asyncio
async def test_spec_carries_ovf_properties(seeded_batch):
    sm = seeded_batch
    fake = FakeProvisioner()
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    await worker.drain_once()

    assert len(fake.calls) == 1
    spec = fake.calls[0]
    assert spec.registry_url == WORKER_ARGS["registry_url"]
    assert spec.goose_image_tag == WORKER_ARGS["goose_image_tag"]
    assert spec.litellm_gateway_url == WORKER_ARGS["litellm_gateway_url"]
    assert spec.heartbeat_url == WORKER_ARGS["heartbeat_url"]
    assert spec.user_token.startswith("tok_")
    assert spec.tenant_id == "t-a"


# ----- Decision 1B: agent_user / agent_user_uid wiring -----------------------


@pytest.mark.asyncio
async def test_spec_carries_sanitized_agent_user_and_derived_uid(sm):
    """The worker must fill agent_user/agent_user_uid on every CloneSpec.

    Regression: the worker only set owner_login, so CloneSpec.agent_user_uid
    kept its default 0 and cloud-init created the agent account as UID 0
    (root) with an unsanitized username.
    """
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="Alice.Wang@example.com", email="aw@x", display_name="AW", tenant_id="t-a"))
        s.add(User(id="admin-1", email="op@x", display_name="Op", tenant_id="t-a", role="admin"))
        s.add(
            Deployment(
                id="dep-u",
                tenant_id="t-a",
                requested_by="admin-1",
                template="tpl",
                image_version="v0.1.0",
                state="pending",
                requested_count=1,
            )
        )
        s.add(
            DeploymentItem(
                deployment_id="dep-u",
                owner_id="Alice.Wang@example.com",
                intended_name="vm-aw",
                **_token_fields("tok_aw"),
            )
        )
        await s.commit()

    fake = FakeProvisioner()
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    await worker.drain_once()

    assert len(fake.calls) == 1
    spec = fake.calls[0]
    assert spec.agent_user == sanitize_username("Alice.Wang@example.com")
    assert spec.agent_user == "alice_wang_example_com"
    assert spec.agent_user_uid == derive_uid("Alice.Wang@example.com")
    assert UID_MIN <= spec.agent_user_uid <= UID_MAX
    assert spec.agent_user_uid != 0


@pytest.mark.asyncio
async def test_unsanitizable_owner_fails_item_instead_of_uid0_clone(sm):
    """An owner_id that can't map to a linux username must fail the item
    (fail closed) — the provisioner must never see a spec with the UID-0
    default."""
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="中文", email="cjk@x", display_name="CJK", tenant_id="t-a"))
        s.add(User(id="admin-1", email="op@x", display_name="Op", tenant_id="t-a", role="admin"))
        s.add(
            Deployment(
                id="dep-cjk",
                tenant_id="t-a",
                requested_by="admin-1",
                template="tpl",
                image_version="v0.1.0",
                state="pending",
                requested_count=1,
            )
        )
        s.add(
            DeploymentItem(
                deployment_id="dep-cjk",
                owner_id="中文",
                intended_name="vm-cjk",
                **_token_fields("tok_cjk"),
            )
        )
        await s.commit()

    fake = FakeProvisioner()
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    for _ in range(MAX_ATTEMPTS + 2):
        await worker.drain_once()

    assert fake.calls == []  # never reached the provisioner
    async with sm() as s:
        item = (await s.execute(select(DeploymentItem))).scalars().one()
        dep = await s.get(Deployment, "dep-cjk")
    assert item.state == "failed"
    assert "linux username" in (item.error_message or "")
    assert dep.failed_count == 1
    assert dep.state == "failed"


# ----- Task 1.11.4: retry-once + educational error messages -----------------


@pytest.mark.asyncio
async def test_transient_failure_retried_and_succeeds(seeded_batch):
    """First attempt fails per-owner exactly once, second succeeds.

    Simulates a vCenter blip: predicate returns "vcenter blip" only on
    the first call per owner, None thereafter. After draining, all 3 VMs
    should be powered_on and the deployment marked completed.
    """
    sm = seeded_batch
    seen: set[str] = set()

    def fail_first(spec):
        if spec.owner_id in seen:
            return None
        seen.add(spec.owner_id)
        return "vcenter blip"

    fake = FakeProvisioner(fail_when=fail_first)
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    # 3 items × 2 attempts = up to 6 drains. Add slack.
    for _ in range(20):
        await worker.drain_once()

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "completed"
        assert dep.succeeded_count == 3
        assert dep.failed_count == 0
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert all(i.state == "powered_on" for i in items)
        # attempts column should reflect the retry
        assert all(i.attempts == 2 for i in items), [i.attempts for i in items]
        # error_message cleared on success
        assert all(i.error_message is None for i in items)


@pytest.mark.asyncio
async def test_persistent_failure_terminates_at_max_attempts(seeded_batch):
    """Always-failing provisioner: each item retried once, then marked failed."""
    sm = seeded_batch
    fake = FakeProvisioner(fail_when=lambda _spec: "nope")
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    for _ in range(20):
        await worker.drain_once()

    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "failed"
        assert dep.failed_count == 3
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        # Each item attempted exactly MAX_ATTEMPTS times
        assert all(i.attempts == MAX_ATTEMPTS for i in items), [i.attempts for i in items]
        assert all(i.state == "failed" for i in items)
        # 6 total calls (3 items × 2 attempts)
        assert len(fake.calls) == 3 * MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_terminal_failure_carries_educational_hint(seeded_batch):
    """Verify the educational message replaces the raw error after final fail."""
    sm = seeded_batch
    # Predicate that always returns a vCenter-shaped error
    fake = FakeProvisioner(fail_when=lambda _spec: "vCenter said: 401 Unauthorized")
    worker = DeploymentWorker(sm, fake, **WORKER_ARGS)
    for _ in range(20):
        await worker.drain_once()

    async with sm() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
    msg = items[0].error_message
    # Raw text preserved
    assert "vCenter said: 401 Unauthorized" in msg
    # Educational hint appended
    assert "rotate the service account" in msg


def test_educational_error_matches_dns_failure():
    msg = educational_error("dial tcp: lookup vc01.example.com: no such host")
    assert "no such host" in msg
    assert "nslookup" in msg


def test_educational_error_matches_permission_failure():
    msg = educational_error("HTTP 403: insufficient permission to clone")
    assert "Service account lacks required role" in msg


def test_educational_error_falls_back_on_unknown():
    msg = educational_error("totally unexpected error 0xDEADBEEF")
    assert "totally unexpected error" in msg
    assert "retried" in msg  # at least notes that a retry happened


@pytest.mark.asyncio
async def test_token_issued_at_stamped_before_provisioner_called(seeded_batch):
    """Decision 8 PR-D: TTL clock starts when the worker hands extraConfig
    to vCenter — measured by stamping just before the clone call."""
    sm = seeded_batch
    worker = DeploymentWorker(sm, FakeProvisioner(), **WORKER_ARGS)
    await worker.drain_once()

    async with sm() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        processed = [i for i in items if i.state in {"powered_on", "ready", "failed"}]
    # At least one item went through the clone phase
    assert processed, "no items reached a post-clone state"
    for item in processed:
        assert item.token_issued_at is not None, (
            f"item {item.id} reached state={item.state} without token_issued_at"
        )


# ----- #350: per-VM secret + heartbeat provisioning on clone success ---------


@pytest.mark.asyncio
async def test_worker_provisions_secrets_and_stamps_heartbeat_hash(seeded_batch):
    """On a successful clone the worker writes per-VM secrets to the store and
    stamps DeploymentItem.heartbeat_token_hash so /api/heartbeat can auth."""
    from agent_platform_control.orchestrator.secret_provisioner import (
        HEARTBEAT_ACCESS_TOKEN,
        SSH_AUTHORIZED_KEYS,
        hash_heartbeat_token,
    )
    from agent_platform_secrets.vm_secret_store import (
        InMemoryVmSecretStore,
        VmSecretRef,
    )

    sm = seeded_batch
    async with sm() as s:
        alice = await s.get(User, "alice")
        alice.ssh_pubkey = "ssh-ed25519 AAAA... alice@laptop"
        await s.commit()

    store = InMemoryVmSecretStore()
    worker = DeploymentWorker(sm, FakeProvisioner(), secret_store=store, **WORKER_ARGS)
    for _ in range(10):
        if await worker.drain_once() == 0:
            break

    async with sm() as s:
        items = {i.owner_id: i for i in (await s.execute(select(DeploymentItem))).scalars().all()}
        alice_item = items["alice"]
        assert alice_item.state == "powered_on"
        assert alice_item.heartbeat_token_hash, "heartbeat_token_hash not stamped"

    # The store holds both per-VM secrets; the heartbeat token's hash matches.
    scope = str(alice_item.id)
    ssh = await store.get(VmSecretRef("vm", scope, SSH_AUTHORIZED_KEYS))
    heartbeat = await store.get(VmSecretRef("vm", scope, HEARTBEAT_ACCESS_TOKEN))
    assert ssh == "ssh-ed25519 AAAA... alice@laptop"
    assert heartbeat and heartbeat.startswith("hbt_")
    assert hash_heartbeat_token(heartbeat) == alice_item.heartbeat_token_hash


@pytest.mark.asyncio
async def test_worker_without_store_skips_provisioning(seeded_batch):
    """No store wired (default) → clone still succeeds, no heartbeat hash."""
    sm = seeded_batch
    worker = DeploymentWorker(sm, FakeProvisioner(), **WORKER_ARGS)
    for _ in range(10):
        if await worker.drain_once() == 0:
            break

    async with sm() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert all(i.state == "powered_on" for i in items)
        assert all(i.heartbeat_token_hash is None for i in items)


@pytest.mark.asyncio
async def test_clone_runs_with_no_db_txn_held(seeded_batch):
    """AC1 (#353): the up-to-600s clone must NOT run inside an open write
    transaction. We prove the prepare phase committed and released before the
    clone IO by reading the item from a *separate* session mid-clone — its
    ``token_issued_at`` (stamped in the prepare phase) must already be visible.
    If the prepare txn were still open across the clone, the stamp would be
    uncommitted and invisible to the second connection.
    """
    sm = seeded_batch
    observed: list[tuple[str, bool]] = []

    class ObservingProvisioner(FakeProvisioner):
        async def clone_vm(self, spec):
            async with sm() as s2:
                item = (
                    await s2.execute(
                        select(DeploymentItem).where(
                            DeploymentItem.intended_name == spec.intended_name
                        )
                    )
                ).scalar_one()
                observed.append((item.state, item.token_issued_at is not None))
            return await super().clone_vm(spec)

    worker = DeploymentWorker(sm, ObservingProvisioner(), **WORKER_ARGS)
    for _ in range(10):
        if await worker.drain_once() == 0:
            break

    assert observed, "clone was never called"
    assert all(state == "cloning" for state, _ in observed)
    assert all(stamped for _, stamped in observed), (
        "token_issued_at not visible mid-clone — prepare txn still held open "
        "across the clone IO (AC1 regression)"
    )

    # Sanity: the batch still completes normally end-to-end.
    async with sm() as s:
        dep = await s.get(Deployment, "dep-1")
        assert dep.state == "completed"
        assert dep.succeeded_count == 3


@pytest.mark.asyncio
async def test_worker_secret_provision_failure_retries_then_fails(seeded_batch):
    """If secret provisioning raises after a successful clone, the item must
    not stay stuck in 'cloning' — it requeues and eventually lands 'failed'.

    The clone created a real VM whose vm_id is rolled back (never persisted),
    so the cleanup cron cannot reap it; the worker must destroy that orphan
    itself or a secret-store outage leaks MAX_ATTEMPTS VMs per item.
    """

    class BoomStore:
        async def put(self, *_a, **_k):
            raise RuntimeError("vaultwarden unreachable")

        async def get(self, *_a, **_k):
            return None

        async def delete(self, *_a, **_k):
            return None

        async def list_vm_refs(self, *_a, **_k):
            return []

    sm = seeded_batch
    provisioner = FakeProvisioner()
    worker = DeploymentWorker(sm, provisioner, secret_store=BoomStore(), **WORKER_ARGS)
    for _ in range(20):
        if await worker.drain_once() == 0:
            break

    async with sm() as s:
        items = (await s.execute(select(DeploymentItem))).scalars().all()
        assert all(i.state == "failed" for i in items)
        assert all("secret provisioning failed" in (i.error_message or "") for i in items)
        # Failed items never persist a vm_id (the write-back was rolled back),
        # so the cleanup cron can't see them.
        assert all(i.vm_id is None for i in items)

    # Every clone created a VM; each provisioning failure must have destroyed
    # exactly that VM — one orphan per attempt, none leaked. With MAX_ATTEMPTS
    # retries there are several clone attempts across the batch.
    assert provisioner.destroyed, "worker leaked the orphan VM (never destroyed)"
    assert len(provisioner.destroyed) == len(provisioner.calls)
    assert len(set(provisioner.destroyed)) == len(provisioner.destroyed)
