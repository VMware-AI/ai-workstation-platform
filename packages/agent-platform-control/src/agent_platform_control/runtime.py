"""Lifespan-managed runtime services (PR-F F-1).

The DeploymentWorker is opt-in (decision 18 PR-F): ``enable_worker=True``
plus an explicit ``provisioner_kind`` are both required, so a forgotten
config never silently brings up a fake provisioner in prod.

Cron + sweeper wiring (cleanup_cron, heartbeat_sweeper, token_expiry)
lands in a follow-up after PR-D (#136) merges — those classes don't
exist on ``origin/main`` yet. The lifespan shape here is what they'll
plug into.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import Settings
from .db.session import get_engine
from .orchestrator import DeploymentWorker
from .orchestrator.cleanup_cron import HeartbeatSweeper
from .orchestrator.fake import FakeProvisioner
from .orchestrator.protocol import Provisioner

logger = logging.getLogger(__name__)


class RuntimeStartupError(RuntimeError):
    """Raised when a required runtime service can't be brought up safely."""


def _build_provisioner(kind: str) -> Provisioner:
    """Factory: map ``AGENT_PLATFORM_PROVISIONER_KIND`` to a concrete impl."""
    normalised = kind.strip().lower()
    if normalised == "fake":
        logger.warning(
            "starting worker with FakeProvisioner — for dev / vcsim only, "
            "set AGENT_PLATFORM_PROVISIONER_KIND=vmware in prod"
        )
        return FakeProvisioner()
    if normalised == "vmware":
        return _build_vmware_provisioner()
    raise RuntimeStartupError(
        f"unknown AGENT_PLATFORM_PROVISIONER_KIND={kind!r}. "
        "Set 'fake' (dev/demo) or 'vmware' (prod, pending follow-up)."
    )


def _build_vmware_provisioner() -> Provisioner:
    """Construct VmwareProvisioner from settings — used when kind='vmware'.

    Fails loudly if any required field is missing so an operator gets a
    teaching error rather than a 30-line pyVmomi stack trace at first clone.

    NOTE: This is the bundle-branch wiring for hands-on vCenter testing.
    Long-term, the cloud-init template path should come from
    ``ImageVersion.template_path`` (PR-A) so an admin can promote new
    image versions without restarting the worker. Until #133 + the
    template_path follow-up land, this single env-driven path is the
    only way to point the provisioner at a real template file.
    """
    # Import here so a non-vmware deployment doesn't pull pyVmomi at startup.
    from .orchestrator.vmware import VmwareProvisioner

    settings = get_settings_fresh()
    missing = [
        name
        for name, value in {
            "AGENT_PLATFORM_VSPHERE_URL": settings.vsphere_url,
            "AGENT_PLATFORM_VSPHERE_USER": settings.vsphere_user,
            "AGENT_PLATFORM_VSPHERE_PASSWORD": settings.vsphere_password,
            "AGENT_PLATFORM_VSPHERE_TEMPLATE": settings.vsphere_template,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeStartupError(
            f"provisioner_kind=vmware needs these env vars set: {', '.join(missing)}. "
            "See docs/runbooks/install-and-test.md §5 for the full .env template."
        )
    try:
        return VmwareProvisioner(
            vcenter_url=settings.vsphere_url,
            vcenter_user=settings.vsphere_user,
            vcenter_password=settings.vsphere_password,
            cloud_init_template_path=settings.vsphere_template,
            verify_ssl=settings.vsphere_verify_ssl,
            clone_timeout_s=settings.vsphere_clone_timeout_s,
        )
    except FileNotFoundError as exc:
        raise RuntimeStartupError(
            f"vCenter template path does not exist: {exc}. "
            "Set AGENT_PLATFORM_VSPHERE_TEMPLATE to a real cloud-init YAML "
            "(e.g. docs/runbooks/cloud-init-bare-ubuntu.yaml in this repo)."
        ) from exc


def get_settings_fresh() -> Settings:
    """Re-read settings without lru_cache so .env edits during dev pick up."""
    from .config import Settings as _Settings

    return _Settings()


def _build_worker(settings: Settings) -> DeploymentWorker:
    """Construct the worker from validated settings. Caller owns lifecycle."""
    if not settings.provisioner_kind:
        raise RuntimeStartupError(
            "enable_worker=True requires AGENT_PLATFORM_PROVISIONER_KIND to be set "
            "('fake' for dev, 'vmware' for prod). Leaving it empty would let "
            "a fake provisioner sneak into prod by accident."
        )

    # SEC-1 (建议1): reject a malformed / default-route ttyd CIDR here, at worker
    # construction (startup), so a misconfig fails loudly (worker disabled,
    # surfaced in deep healthz) instead of ValueError-ing on every clone at
    # runtime. Same validator the clone path uses — normalises the value too.
    from .orchestrator.vmware import _validate_ttyd_allow_cidr

    try:
        ttyd_allow_cidr = _validate_ttyd_allow_cidr(settings.worker_ttyd_allow_cidr)
    except ValueError as exc:
        raise RuntimeStartupError(f"AGENT_PLATFORM_WORKER_TTYD_ALLOW_CIDR invalid: {exc}") from exc

    provisioner = _build_provisioner(settings.provisioner_kind)
    engine = get_engine()
    sm = async_sessionmaker(engine, expire_on_commit=False)
    # #350: hand the worker the SAME process-wide VmSecretStore the cloud-init
    # exchange endpoint reads from. The worker writes ssh + heartbeat secrets at
    # clone time; the in-VM cloud-init redeems them via POST /api/cloud-init/
    # exchange-token. A separate store instance would make every exchange miss.
    from .api.cloud_init import get_vm_secret_store

    return DeploymentWorker(
        sm,
        provisioner,
        registry_url=settings.worker_registry_url,
        goose_image_tag=settings.worker_goose_image_tag,
        litellm_gateway_url=settings.worker_litellm_gateway_url,
        heartbeat_url=settings.worker_heartbeat_url,
        ttyd_allow_cidr=ttyd_allow_cidr,
        poll_interval_s=settings.worker_poll_interval_s,
        secret_store=get_vm_secret_store(),
    )


def _build_heartbeat_sweeper(settings: Settings) -> HeartbeatSweeper:
    """Construct the HeartbeatSweeper from settings. Caller owns lifecycle."""
    engine = get_engine()
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return HeartbeatSweeper(sm)


@asynccontextmanager
async def managed_runtime(settings: Settings) -> AsyncIterator[dict[str, object]]:
    """Yields a dict of started services so app.lifespan can hand them off
    to the request handlers via ``app.state``.

    Shape:
        {"worker": DeploymentWorker | None,
         "heartbeat_sweeper": HeartbeatSweeper | None}

    The heartbeat sweeper rides the same ``enable_worker`` gate as the worker
    (#350): without the worker stamping ``heartbeat_token_hash`` there are no
    heartbeats to sweep, so the two share a lifecycle. The failed-vm cleanup
    cron stays a placeholder until its decision-5 recovery runbook is signed
    off (it destroys customer VMs).
    """
    started: dict[str, object] = {"worker": None, "heartbeat_sweeper": None}

    if settings.enable_worker:
        try:
            worker = _build_worker(settings)
            worker.start()
            started["worker"] = worker
            logger.info("DeploymentWorker started")
        except RuntimeStartupError as exc:
            # Log + continue — a fail-loud at startup would deadlock the ASGI
            # boot. Record the cause so /healthz/deep surfaces it as degraded
            # ("failed"), not indistinguishable from a deliberately-off worker.
            logger.exception("DeploymentWorker startup failed: %s", exc)
            started["worker_error"] = str(exc)

        # Only start the sweeper if the worker is actually up — a sweeper with
        # no worker to feed it heartbeat hashes would just spin uselessly.
        if started.get("worker") is not None:
            sweeper = _build_heartbeat_sweeper(settings)
            sweeper.start()
            started["heartbeat_sweeper"] = sweeper
            logger.info("HeartbeatSweeper started")

    try:
        yield started
    finally:
        sweeper = started.get("heartbeat_sweeper")
        if sweeper is not None:
            try:
                await sweeper.stop()
                logger.info("HeartbeatSweeper stopped")
            except Exception:
                logger.exception("HeartbeatSweeper shutdown failed (continuing)")
        worker = started.get("worker")
        if worker is not None:
            try:
                await worker.stop()
                logger.info("DeploymentWorker stopped")
            except Exception:
                logger.exception("DeploymentWorker shutdown failed (continuing)")
