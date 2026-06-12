"""DeploymentWorker — asyncio background task that drains pending items.

Design notes:
  - Single coroutine per worker; no multi-process. PoC scale is ~tens of VMs/min.
  - Pulls items in state=pending, marks cloning, calls Provisioner, then writes
    powered_on or failed. Updates Deployment counts in the same transaction.
  - Claims are atomic (conditional UPDATE ... WHERE state='pending', checked
    by rowcount), and the cloud-init sweep claims each transition the same way,
    so multiple workers behind one DB do not double-process or double-count.
  - An item left in 'cloning' by a crashed worker is reset to 'pending' on the
    next worker startup (recover_stale_cloning, bounded by a lease) so it is
    retried rather than orphaned forever.
  - Each loop pulls one item — no concurrent clones from a single worker.
  - Task 1.11.4: on first failure we requeue (state=pending) so the next
    claim attempts again — transient errors (vCenter API blip, IP pool
    race) usually clear on retry. Second failure becomes terminal with an
    educational error message that suggests where to look.

The worker is started by app lifespan when AGENT_PLATFORM_ENABLE_WORKER=1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from agent_platform_secrets.vm_secret_store import VmSecretStore
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import VM, Deployment, DeploymentItem, User
from .agent_user import AgentUserError, derive_uid, sanitize_username
from .audit import record_audit
from .deployment_state import recompute_deployment_state
from .protocol import CloneResult, CloneSpec, Provisioner
from .secret_provisioner import hash_heartbeat_token, provision_per_vm_secrets
from .tokens import decrypt_user_token

logger = logging.getLogger("agent_platform_control.orchestrator")

# Task 1.11.4: total tries before a deployment item is marked failed.
# Set to 2 because (a) every clone burns vCenter quota so retrying more
# than once hurts neighbours, and (b) operator pattern in this team is
# "one retry to dodge transients, then page the human."
MAX_ATTEMPTS = 2


# Educational error hints — substring → "raw + suggested next step".
# Patterns come from real vCenter / cloud-init / NSX failure modes we've
# seen in the vmware-aiops install logs; ordered most-specific first so
# the first match wins.
_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    (
        "no such host",
        "DNS lookup failed for vCenter — check FQDN spelling, or set "
        "AGENT_PLATFORM_VCENTER_HOST to a plain IP. Run `nslookup <host>` from "
        "the control plane to confirm.",
    ),
    (
        "name resolution",
        "DNS lookup failed for vCenter — check FQDN spelling, or set "
        "AGENT_PLATFORM_VCENTER_HOST to a plain IP.",
    ),
    (
        "401",
        "vCenter rejected credentials — rotate the service account "
        "password or verify AGENT_PLATFORM_VCENTER_USER matches an active SSO user.",
    ),
    (
        "unauthorized",
        "vCenter rejected credentials — rotate the service account "
        "password or verify AGENT_PLATFORM_VCENTER_USER matches an active SSO user.",
    ),
    (
        "403",
        "Service account lacks required role — needs at least "
        "VirtualMachine.Provisioning.Clone on the template AND the target "
        "resource pool. Check vCenter Roles & Permissions.",
    ),
    (
        "permission",
        "Service account lacks required role — needs at least "
        "VirtualMachine.Provisioning.Clone on the template AND the target "
        "resource pool.",
    ),
    (
        "template",
        "VM template not found in inventory — verify the template path "
        "matches an entry in vCenter (case-sensitive). Worker uses "
        "Deployment.template verbatim.",
    ),
    (
        "timeout",
        "Operation timed out — check vCenter UI for queued tasks; large "
        "templates on slow datastores can need >10 min. Consider raising "
        "the provisioner timeout if this is consistent.",
    ),
    (
        "duplicate",
        "Name already exists in inventory — pick a different "
        "DeploymentItem.intended_name or delete the orphan VM first.",
    ),
)


def educational_error(raw: str) -> str:
    """Wrap a raw provisioner error with a substring-matched fix suggestion.

    Falls back to the raw message + "(retried 1 time)" suffix so the
    operator knows the failure was already retried — no need to manually
    retry once more before paging.
    """
    text = raw.lower()
    for needle, suggestion in _ERROR_HINTS:
        if needle in text:
            return f"{raw}\n  → {suggestion}"
    return f"{raw}\n  → (retried {MAX_ATTEMPTS - 1} time before giving up)"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _elapsed_seconds(now: datetime, since: datetime) -> float:
    """Seconds between ``since`` and ``now``, tolerant of naive timestamps.

    SQLite hands ``DateTime(timezone=True)`` columns back as naive datetimes,
    so we coerce a missing tzinfo to UTC before subtracting to avoid a
    "can't subtract offset-naive and offset-aware" TypeError.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return (now - since).total_seconds()


class DeploymentWorker:
    """Drain pending DeploymentItems. Start with `.start()`, stop with `.stop()`."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        provisioner: Provisioner,
        *,
        registry_url: str,
        goose_image_tag: str,
        litellm_gateway_url: str,
        heartbeat_url: str,
        ttyd_allow_cidr: str = "",
        poll_interval_s: float = 1.0,
        wait_cloud_init: bool = False,
        cloud_init_timeout_s: float = 600.0,
        stale_cloning_lease_s: float = 900.0,
        secret_store: VmSecretStore | None = None,
    ) -> None:
        self._sm = sessionmaker
        self._provisioner = provisioner
        self._registry_url = registry_url
        self._goose_image_tag = goose_image_tag
        self._litellm_gateway_url = litellm_gateway_url
        self._heartbeat_url = heartbeat_url
        # PR-C/PR-D wiring (#350): write per-VM ssh + heartbeat secrets to the
        # SAME store the cloud-init exchange endpoint reads from, so a freshly
        # cloned VM can redeem them. None disables provisioning — kept optional
        # so in-process tests that don't exercise the secret path stay simple.
        self._secret_store = secret_store
        # SEC-1: trusted-network CIDR for the in-VM ttyd port. Empty (default)
        # keeps ttyd firewalled — see install-agent.sh + _render_userdata.
        self._ttyd_allow_cidr = ttyd_allow_cidr
        self._poll_interval_s = poll_interval_s
        # An item left in 'cloning' longer than this is presumed orphaned by a
        # crashed worker and reset to 'pending' on startup (PR-review #81).
        self._stale_cloning_lease_s = stale_cloning_lease_s
        # Task 1.11.3: when on, a powered-on VM is not "succeeded" until its
        # in-VM cloud-init redeems the bootstrap token (token_consumed_at).
        # Off by default so the fake-provisioner test/demo path (which never
        # boots a real VM) still completes. Wired from AGENT_PLATFORM_WAIT_CLOUD_INIT
        # once the worker lifespan wiring lands.
        self._wait_cloud_init = wait_cloud_init
        self._cloud_init_timeout_s = cloud_init_timeout_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def provisioner(self) -> Provisioner:
        """The live Provisioner — force-redeploy (#216) borrows it via app.state."""
        return self._provisioner

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="deployment-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def drain_once(self) -> int:
        """Process one pending item, if any. Returns 1 if work was done, 0 if idle.

        Public for tests — avoids hammering the loop with sleeps.
        """
        item_id = await self._claim_one()
        if item_id is not None:
            await self._process_item(item_id)
            return 1
        # No clone work left. With cloud-init wait on, advance any powered-on
        # items whose in-VM bootstrap has finished (or timed out).
        if self._wait_cloud_init:
            return await self._advance_cloud_init()
        return 0

    async def _run(self) -> None:
        logger.info("deployment-worker started")
        recovered = await self.recover_stale_cloning()
        if recovered:
            logger.warning("recovered %s item(s) orphaned in 'cloning' by a prior crash", recovered)
        while not self._stop_event.is_set():
            try:
                processed = await self.drain_once()
            except Exception:
                logger.exception("deployment-worker loop error")
                processed = 0
            if processed == 0:
                # idle backoff; cooperate with stop_event
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), self._poll_interval_s)
        logger.info("deployment-worker stopped")

    async def recover_stale_cloning(self) -> int:
        """Reset items stranded in 'cloning' past the lease back to 'pending'.

        A worker that dies mid-clone leaves its item in 'cloning'; since
        :meth:`_claim_one` only picks 'pending' rows, that item is otherwise
        never retried (PR-review #81). Run on startup. ``attempts`` was already
        bumped at claim time, so a recovered item that fails again still hits
        the MAX_ATTEMPTS terminal path.
        """
        cutoff = _utcnow() - timedelta(seconds=self._stale_cloning_lease_s)
        async with self._sm() as s, s.begin():
            res = await s.execute(
                update(DeploymentItem)
                .where(
                    DeploymentItem.state == "cloning",
                    DeploymentItem.updated_at < cutoff,
                )
                .values(state="pending", updated_at=_utcnow())
            )
            return res.rowcount or 0

    async def _claim_one(self) -> int | None:
        """Atomically pick a pending item and mark it cloning. Returns item id or None.

        The claim is the conditional UPDATE itself (``WHERE state='pending'``):
        if a second worker grabbed the row between our SELECT and UPDATE, the
        UPDATE matches 0 rows and we report idle rather than double-processing
        the same item (PR-review #81 claim race).
        """
        async with self._sm() as s, s.begin():
            row = (
                await s.execute(
                    select(DeploymentItem.id)
                    .where(DeploymentItem.state == "pending")
                    .order_by(DeploymentItem.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            result = await s.execute(
                update(DeploymentItem)
                .where(DeploymentItem.id == row, DeploymentItem.state == "pending")
                .values(state="cloning", attempts=DeploymentItem.attempts + 1, updated_at=_utcnow())
            )
            if result.rowcount != 1:
                # Lost the race — another worker claimed it first.
                return None
            return row

    async def _process_item(self, item_id: int) -> None:
        async with self._sm() as s:
            item = await s.get(DeploymentItem, item_id)
            if item is None:
                return
            dep = await s.get(Deployment, item.deployment_id)
            if dep is None:
                return
            owner_login = item.owner_id

            # Decision 1B: derive the in-VM linux account from the owner id.
            # Without these, CloneSpec defaults render AGENT_USER_UID=0 (root)
            # and an unsanitized username into cloud-init. Fail closed: an
            # owner id with no valid linux-name mapping retires the item via
            # the normal failure path instead of reaching the provisioner.
            try:
                agent_user = sanitize_username(item.owner_id)
                agent_user_uid = derive_uid(item.owner_id)
            except AgentUserError as exc:
                logger.warning("item %s owner %r: %s", item_id, item.owner_id, exc)
                await self._apply_result(s, item, dep, CloneResult(success=False, error=str(exc)))
                await s.commit()
                return

            spec = CloneSpec(
                intended_name=item.intended_name,
                template=dep.template,
                tenant_id=dep.tenant_id,
                owner_id=item.owner_id,
                owner_login=owner_login,
                agent_user=agent_user,
                agent_user_uid=agent_user_uid,
                image_version=dep.image_version,
                registry_url=self._registry_url,
                goose_image_tag=self._goose_image_tag,
                litellm_gateway_url=self._litellm_gateway_url,
                user_token=decrypt_user_token(item.user_token_enc),
                heartbeat_url=self._heartbeat_url,
                ttyd_allow_cidr=self._ttyd_allow_cidr,
            )

            # Decision 8: stamp the TTL clock just before handing extraConfig to
            # vCenter. We accept the few-millisecond delta vs the exact moment
            # CloneVM_Task receives it — both are within the same session and
            # the 30-minute budget is generous.
            item.token_issued_at = _utcnow()

            try:
                result = await self._provisioner.clone_vm(spec)
            except Exception as exc:
                result = CloneResult(success=False, error=f"provisioner raised: {exc!r}")
                logger.exception("provisioner crashed for item %s", item_id)

            await self._apply_result(s, item, dep, result)

            # PR-C/PR-D (#350): on a successful clone, provision the per-VM
            # secrets (ssh_authorized_keys + heartbeat_access_token) and stamp
            # the heartbeat token's hash on the item so POST /api/heartbeat can
            # authenticate the in-VM agent. Same transaction as the result
            # write-back: if provisioning fails the clone result is rolled back
            # with it and the item is retried/failed via the normal path — a VM
            # with no redeemable secrets is worse than a retry (matches PR #349's
            # fail-closed agent_user handling).
            if item.state == "powered_on" and self._secret_store is not None:
                try:
                    await self._provision_secrets(s, item)
                except Exception as exc:
                    logger.exception("secret provisioning failed for item %s", item_id)
                    # The clone created a real VM, but its vm_id lives only in
                    # this (about-to-be-rolled-back) transaction — it is never
                    # persisted, so the failed-vm cleanup cron, which filters
                    # vm_id IS NOT NULL, can never reap it. Destroy it now,
                    # best-effort, before discarding the write-back; otherwise a
                    # persistent secret-store outage leaks up to MAX_ATTEMPTS
                    # orphan VMs per item.
                    cloned_vm_id = result.vm_id
                    await s.rollback()
                    destroyed = await self._destroy_orphan_vm(cloned_vm_id, item_id)
                    await self._fail_after_provision_error(item_id, exc, cloned_vm_id, destroyed)
                    return

            await s.commit()

    async def _provision_secrets(self, s: AsyncSession, item: DeploymentItem) -> None:
        """Write per-VM secrets + stamp the heartbeat hash. Caller owns commit.

        Reads the owner's uploaded SSH public key from ``users.ssh_pubkey``
        (set via ``PUT /api/me/ssh-pubkey``); a user who never uploaded one
        gets an empty ``authorized_keys`` and can add a key + reclone later.
        """
        assert self._secret_store is not None  # guarded by caller  # nosec B101
        owner = await s.get(User, item.owner_id)
        owner_ssh_pubkey = owner.ssh_pubkey if owner is not None else None
        provisioned = await provision_per_vm_secrets(
            self._secret_store,
            deployment_item_id=item.id,
            owner_ssh_pubkey=owner_ssh_pubkey,
        )
        item.heartbeat_token_hash = hash_heartbeat_token(provisioned.heartbeat_token)

    async def _destroy_orphan_vm(self, vm_id: str | None, item_id: int) -> bool:
        """Best-effort destroy a just-cloned VM after secret provisioning failed.

        The clone write-back was rolled back, so ``vm_id`` is never persisted
        and the failed-vm cleanup cron (which filters ``vm_id IS NOT NULL``)
        cannot reap this VM. Destroy it here so a persistent secret-store outage
        cannot leak orphan VMs. A destroy failure is logged but never raised —
        the caller still retires the item via the failure path. Returns ``True``
        iff the VM was destroyed.
        """
        if not vm_id:
            return False
        try:
            await self._provisioner.destroy_vm(vm_id)
        except Exception:
            logger.exception(
                "could not destroy orphan VM %s (item %s) after provisioning "
                "failure — it may linger in vCenter and is NOT reapable by the "
                "cleanup cron (vm_id never persisted); manual cleanup may be needed",
                vm_id,
                item_id,
            )
            return False
        logger.info(
            "destroyed orphan VM %s (item %s) after secret provisioning failure",
            vm_id,
            item_id,
        )
        return True

    async def _fail_after_provision_error(
        self,
        item_id: int,
        exc: Exception,
        cloned_vm_id: str | None = None,
        orphan_destroyed: bool = False,
    ) -> None:
        """Retire an item whose clone succeeded but secret provisioning failed.

        Runs in a fresh transaction (the clone write-back was rolled back) so
        the item lands in ``failed`` with an educational error rather than
        sitting in ``cloning`` forever. The just-cloned VM was destroyed
        best-effort by the caller (its vm_id is never persisted, so the cleanup
        cron cannot reap it); the destroy outcome is audited here.
        """
        async with self._sm() as s, s.begin():
            item = await s.get(DeploymentItem, item_id)
            if item is None:
                return
            dep = await s.get(Deployment, item.deployment_id)
            if dep is None:
                return
            await self._apply_result(
                s,
                item,
                dep,
                CloneResult(success=False, error=f"secret provisioning failed: {exc!r}"),
            )
            if cloned_vm_id:
                # Auto-destroy must be traceable (harness #H-7), same as the
                # cleanup cron's vm.destroy audit.
                record_audit(
                    s,
                    actor="worker:provision",
                    operation="vm.destroy",
                    resource=cloned_vm_id,
                    params={
                        "deployment_item_id": item_id,
                        "reason": "orphan-after-secret-provision-failure",
                    },
                    result="success" if orphan_destroyed else "failure",
                )

    async def _apply_result(
        self, s: AsyncSession, item: DeploymentItem, dep: Deployment, result: CloneResult
    ) -> None:
        # ``dep`` was loaded before the (slow, awaited) clone. The worker shares
        # the event loop with the API, so a POST /{id}/cancel can have committed
        # in the meantime — re-read the authoritative state before deciding, or
        # we would revert the cancel and resurrect the item (PR-review #81). The
        # counter bumps below go through atomic SQL UPDATEs for the same reason
        # the cloud-init sweep does (no read-modify-write on a stale snapshot).
        await s.refresh(dep)

        def _bump(column: str) -> None:
            return s.execute(
                update(Deployment)
                .where(Deployment.id == dep.id)
                .values({column: getattr(Deployment, column) + 1})
            )

        if result.success and result.vm_id:
            # Create VM row tied to this item.
            vm = VM(
                id=result.vm_id,
                name=item.intended_name,
                tenant_id=dep.tenant_id,
                owner_id=item.owner_id,
                image_version=dep.image_version,
                state="running",
                ip_address=result.ip_address,
            )
            s.add(vm)
            item.vm_id = result.vm_id
            item.state = "powered_on"
            item.error_message = None
            # Task 1.11.3: with cloud-init wait on, powered_on is not yet
            # success — _advance_cloud_init() bumps succeeded once the in-VM
            # bootstrap redeems its token. Without the wait, it is terminal.
            if not self._wait_cloud_init:
                await _bump("succeeded_count")
        elif dep.state == "cancelled":
            # The deployment was cancelled while this item was in flight and
            # the clone did not succeed — retire it as cancelled instead of
            # requeueing (which would resurrect work the admin cancelled) or
            # counting it as a failure (PR-review #81).
            item.state = "cancelled"
            item.error_message = result.error or None
            await _bump("cancelled_count")
            logger.info(
                "deployment item %s not requeued — parent deployment cancelled",
                item.id,
            )
        elif item.attempts < MAX_ATTEMPTS:
            # Task 1.11.4: requeue for one more shot. Keep the raw error in
            # error_message so the operator can still see what the first
            # attempt hit if the retry also fails (overwrites below) or
            # succeeds (cleared above). Do NOT bump failed_count — the item
            # is not terminal yet.
            item.state = "pending"
            item.error_message = result.error or "unknown error"
            logger.info(
                "deployment item %s failed on attempt %s; requeueing (raw=%s)",
                item.id,
                item.attempts,
                (result.error or "")[:120],
            )
        else:
            item.state = "failed"
            item.error_message = educational_error(result.error or "unknown error")
            await _bump("failed_count")

        item.updated_at = _utcnow()
        # Re-read the counts the atomic bumps just wrote, then recompute.
        await s.refresh(dep)
        recompute_deployment_state(dep)

    async def _advance_cloud_init(self) -> int:
        """Advance powered-on items based on their cloud-init outcome.

        For every item parked in ``powered_on``:
          * ``token_consumed_at`` set  → ``ready`` + succeeded_count += 1
          * timeout elapsed, never set → ``failed`` + failed_count += 1
          * otherwise                  → leave untouched (clock keeps running)

        Returns the number of items advanced this pass — cloud-init checks are
        cheap DB reads, so we sweep all of them at once (no per-item starvation
        if the lowest-id item is still waiting).
        """
        advanced = 0
        async with self._sm() as s, s.begin():
            items = (
                (
                    await s.execute(
                        select(DeploymentItem)
                        .where(DeploymentItem.state == "powered_on")
                        .order_by(DeploymentItem.id)
                    )
                )
                .scalars()
                .all()
            )
            now = _utcnow()
            touched: set[str] = set()

            for item in items:
                ready = item.token_consumed_at is not None
                timed_out = (
                    not ready
                    and _elapsed_seconds(now, item.updated_at) > self._cloud_init_timeout_s
                )
                if not ready and not timed_out:
                    continue  # clock still running; leave it

                if ready:
                    new_state, error_message = "ready", None
                else:
                    new_state = "failed"
                    error_message = educational_error(
                        f"VM {item.vm_id or item.intended_name} powered on but cloud-init "
                        f"never redeemed its bootstrap token within "
                        f"{int(self._cloud_init_timeout_s)}s — check the in-VM cloud-init "
                        f"logs (/var/log/cloud-init-output.log) and that the VM can reach "
                        f"POST /api/cloud-init/exchange-token on the control plane."
                    )

                # Atomic claim of the transition: only the worker whose UPDATE
                # actually flips powered_on -> new_state owns the count bump, so
                # two workers sweeping at once cannot double-count (#130).
                res = await s.execute(
                    update(DeploymentItem)
                    .where(
                        DeploymentItem.id == item.id,
                        DeploymentItem.state == "powered_on",
                    )
                    .values(state=new_state, error_message=error_message, updated_at=now)
                )
                if res.rowcount != 1:
                    continue  # another worker advanced this item first

                col = "succeeded_count" if ready else "failed_count"
                await s.execute(
                    update(Deployment)
                    .where(Deployment.id == item.deployment_id)
                    .values({col: getattr(Deployment, col) + 1})
                )
                touched.add(item.deployment_id)
                advanced += 1

            # Recompute terminality with fresh counts (the atomic increments
            # above bypassed the identity map, so reload before reading).
            for dep_id in touched:
                dep = await s.get(Deployment, dep_id)
                if dep is not None:
                    await s.refresh(dep)
                    recompute_deployment_state(dep)
        return advanced
