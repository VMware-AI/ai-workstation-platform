"""pyVmomi-based provisioner — real vCenter and vcsim.

Uses pyVmomi directly (not vmware-aiops MCP layer, which targets agents not
async Python). Honors vmware-skill family 踩坑 #32: never setattr on a
pyVmomi ManagedObject; keep per-SI metadata in a module-level dict keyed
by id(si).

OVF property injection strategy:
    Pre-substitute the cloud-init userdata template in C1, base64-encode it,
    and attach via VirtualMachine extraConfig as:
        guestinfo.userdata          (base64 of rendered cloud-init yaml)
        guestinfo.userdata.encoding = "base64"
        guestinfo.metadata          (base64 of instance-id metadata)
        guestinfo.metadata.encoding = "base64"
    The C3 golden image runs cloud-init with the VMware GuestInfo datasource
    enabled so it reads these on first boot.

Connection caching:
    SmartConnect is expensive (~1-2s) and TLS-heavy. We cache one
    ServiceInstance per (host:port, user) tuple. Dead SIs are evicted on
    next use.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import contextlib
import ipaddress
import logging
import re
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from agent_platform_telemetry_shim import CircuitBreaker
from pyVim import connect as vim_connect
from pyVmomi import vim

from .audit import sanitize
from .protocol import CloneResult, CloneSpec, Provisioner

logger = logging.getLogger("agent_platform_control.orchestrator.vmware")

# Per-SI metadata side-store. See vmware-skill 踩坑 #32 — DO NOT setattr on
# pyVmomi ManagedObjects (read-only in pyVmomi 8.x).
# Lock guards "check then set" against concurrent asyncio.to_thread workers.
_SI_CACHE: dict[tuple[str, str], object] = {}
_SI_META: dict[int, dict[str, object]] = {}
# Global lock guards only the dicts themselves (microsecond holds). The slow
# part — SmartConnect, ~1-2s — runs under a per-target lock so a dead vCenter
# can't queue healthy targets or the breaker's fast-fail behind it (#226).
_SI_LOCK = threading.Lock()
# One lock per (host:port, user); never pruned — the vCenter target set is
# small and static (config-defined), same lifetime assumption as _BREAKERS.
_SI_LOCKS: dict[tuple[str, str], threading.Lock] = {}

# H-14 (#215): per-target circuit breaker around SmartConnect. On a vCenter
# outage, 3 consecutive connect failures trip the breaker and subsequent
# clones fast-fail with a teaching CircuitBreakerError instead of each
# burning a full connect timeout. Per-target so one dead vCenter doesn't
# fast-fail the others (M2 multi-vCenter). Mutated only under _SI_LOCK,
# like the SI cache.
_BREAKERS: dict[tuple[str, str], CircuitBreaker] = {}
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_RESET_AFTER_S = 30.0


def _target_lock(key: tuple[str, str]) -> threading.Lock:
    with _SI_LOCK:
        return _SI_LOCKS.setdefault(key, threading.Lock())


def _get_breaker(key: tuple[str, str]) -> CircuitBreaker:
    with _SI_LOCK:
        return _BREAKERS.setdefault(
            key,
            CircuitBreaker(
                failure_threshold=_BREAKER_FAILURE_THRESHOLD,
                reset_after_s=_BREAKER_RESET_AFTER_S,
                name=f"vCenter {key[0]}",
            ),
        )


def _cleanup() -> None:
    with _SI_LOCK:
        for si in list(_SI_CACHE.values()):
            with contextlib.suppress(Exception):
                vim_connect.Disconnect(si)
        _SI_CACHE.clear()
        _SI_META.clear()


atexit.register(_cleanup)


@dataclass(frozen=True)
class _Target:
    host: str
    port: int
    user: str
    password: str
    verify_ssl: bool


def _connect(target: _Target, *, bypass_breaker: bool = False):
    """Return a cached or fresh ServiceInstance for target.

    Thread-safe under asyncio.to_thread fan-out: a per-target lock covers
    the "miss → SmartConnect → store" critical section so two threads don't
    both open a connection and leak one — while other targets (and this
    target's breaker fast-fail) proceed unblocked (#226). The global _SI_LOCK
    only guards the dict accesses themselves.

    ``bypass_breaker=True`` is for diagnostic callers only (admin health
    probe): the connect attempt neither consults nor counts toward the
    per-target circuit breaker. Auth failures (bad credentials) DO count on
    the normal path — 3 of those open the breaker just like an outage; the
    first failures' results carry the root cause.
    """
    key = (f"{target.host}:{target.port}", target.user)
    breaker = None if bypass_breaker else _get_breaker(key)
    # Fast-fail BEFORE queuing on the per-target lock (#226): while a real
    # connect attempt (trip-burning or probe) hangs inside the critical
    # section, other callers for this target must still get their instant
    # CircuitBreakerError. peek_open() is read-only — the single-probe
    # transition stays inside call_sync, under the breaker's own lock.
    if breaker is not None and breaker.peek_open():
        raise breaker.open_error()

    with _target_lock(key):
        with _SI_LOCK:
            si = _SI_CACHE.get(key)
        if si is not None:
            try:
                si.RetrieveContent()
                return si
            except Exception:
                logger.info("cached SI for %s is dead; reconnecting", key)
                with _SI_LOCK:
                    _SI_CACHE.pop(key, None)
                    _SI_META.pop(id(si), None)

        if target.verify_ssl:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl._create_unverified_context()  # noqa: S323  # explicit verify_ssl=False opt-in for self-signed labs  # nosec B323 — explicit verify_ssl=False opt-in

        def _smart_connect():
            return vim_connect.SmartConnect(
                host=target.host,
                port=target.port,
                user=target.user,
                pwd=target.password,
                sslContext=ctx,
            )

        # breaker is None on the diagnostic path (admin health probe): always
        # attempt the real connection so operators see live vCenter state even
        # while the breaker is open, and a probe failure doesn't count against
        # the provisioning path's breaker (PR #221 review HIGH-2).
        si = _smart_connect() if breaker is None else breaker.call_sync(_smart_connect)
        with _SI_LOCK:
            _SI_CACHE[key] = si
            _SI_META[id(si)] = {"verify_ssl": target.verify_ssl}
        return si


def get_si_verify_ssl(si: object) -> bool:
    """Public accessor for downstream code (guest ops, OVA deploy) that needs
    to know whether the cached SI was opened with verify_ssl=True/False."""
    return bool(_SI_META.get(id(si), {}).get("verify_ssl", True))


# extraConfig key stamped on every clone so the idempotency path can tell a VM
# this platform provisioned from an unrelated VM that merely shares the name.
PLATFORM_MANAGED_KEY = "guestinfo.agent_platform.managed"


def _is_platform_managed(vm) -> bool:
    """True if ``vm`` carries the Agent Platform extraConfig marker."""
    try:
        for opt in vm.config.extraConfig or []:
            if opt.key == PLATFORM_MANAGED_KEY and str(opt.value) == "1":
                return True
    except Exception:
        return False
    return False


def _find_vm_by_name(content, name: str):
    """Return a *platform-managed* VM with this exact name, or None.

    Used for the clone_vm idempotency check. It deliberately matches only VMs
    this platform provisioned (carrying the extraConfig marker), so a name
    collision with an unrelated VM is never adopted as "our" clone — which
    would later let destroy_vm delete a VM we never created (PR-review #103).
    A genuine collision instead falls through to the clone, which surfaces
    vCenter's DuplicateName error honestly.
    """
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for candidate in view.view:
            if candidate.name == name and _is_platform_managed(candidate):
                return candidate
    finally:
        view.Destroy()
    return None


def _find_template(si, template_path: str):
    """Resolve a template by inventory path, with a flat-name fallback for vcsim."""
    content = si.RetrieveContent()
    found = content.searchIndex.FindByInventoryPath(template_path)
    if found is not None:
        return found

    name = template_path.split("/")[-1]
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        non_template_match = None
        for candidate in view.view:
            if candidate.name != name:
                continue
            # Prefer a real template; a live VM sharing the basename must not
            # be cloned in its place (PR-review #82). Keep the first plain-VM
            # match only as a last resort (vcsim defaults aren't templates).
            if getattr(candidate.config, "template", False):
                return candidate
            if non_template_match is None:
                non_template_match = candidate
    finally:
        view.Destroy()
    if non_template_match is not None:
        logger.warning(
            "flat-name fallback resolved %r to a non-template VM %r — clone "
            "source should be a marked template; verify the inventory path",
            name,
            non_template_match.name,
        )
    return non_template_match


def _build_extra_config(rendered_userdata: str, instance_id: str):
    userdata_b64 = base64.b64encode(rendered_userdata.encode("utf-8")).decode("ascii")
    metadata_b64 = base64.b64encode(
        f"instance-id: {instance_id}\nlocal-hostname: {instance_id}\n".encode()
    ).decode("ascii")
    return [
        vim.option.OptionValue(key="guestinfo.userdata", value=userdata_b64),
        vim.option.OptionValue(key="guestinfo.userdata.encoding", value="base64"),
        vim.option.OptionValue(key="guestinfo.metadata", value=metadata_b64),
        vim.option.OptionValue(key="guestinfo.metadata.encoding", value="base64"),
        # Provenance marker — see _is_platform_managed / _find_vm_by_name (#103).
        vim.option.OptionValue(key=PLATFORM_MANAGED_KEY, value="1"),
    ]


def _validate_ttyd_allow_cidr(raw: str) -> str:
    """Validate the SEC-1 ttyd trusted-network CIDR before it lands in install.env.

    Empty is allowed and means "no trusted network" — install-agent.sh then
    fails closed and leaves ttyd:7681 firewalled. A non-empty value is spliced
    into a ``KEY=VALUE`` env line that bash sources and passes to ``ufw allow
    from``, so reject anything that is not a bare IPv4/IPv6 address or CIDR
    (a stray newline or shell metacharacter would corrupt the env file or the
    firewall rule). A single address normalises to a /32 or /128.
    """
    candidate = raw.strip()
    if not candidate:
        return ""
    # Reject IPv6 zone ids up front: ip_network(strict=False) accepts a scoped
    # address (fe80::1%eth0) and preserves the zone string verbatim through
    # str(), so `fe80::1%"; ufw allow` would slip past and corrupt install.env /
    # the ufw rule. A scoped/link-local source is never valid for `ufw allow
    # from` anyway (review HIGH).
    if "%" in candidate:
        raise ValueError(
            f"TTYD_ALLOW_CIDR {raw!r} contains an IPv6 zone id ('%…') — a scoped / "
            "link-local source is never valid for a ufw allow rule. Use a plain "
            "trusted segment like 10.20.0.0/16, or leave it unset to keep ttyd "
            "firewalled."
        )
    try:
        # strict=False so a host address (10.0.0.5) is accepted, not just a
        # network address. ip_network rejects newlines, spaces, and metachars —
        # except IPv6 zone ids, which we strip above.
        net = ipaddress.ip_network(candidate, strict=False)
    except ValueError as exc:
        raise ValueError(
            f"TTYD_ALLOW_CIDR {raw!r} is not a valid IP address or CIDR — it is "
            "spliced into the in-VM install.env / ufw rule; leave it unset to keep "
            "ttyd firewalled (fail closed)."
        ) from exc
    # SEC-1 hardening: a /0 (0.0.0.0/0 or ::/0) opens the in-VM ttyd:7681 shell to
    # the entire network with no auth — the exact thing fail-closed guards against,
    # and almost always a misconfig. Reject the default route explicitly; an
    # operator who truly means a public segment must name it, not the /0 catch-all.
    if net.prefixlen == 0:
        raise ValueError(
            f"TTYD_ALLOW_CIDR {raw!r} is the default route (/0) — it would open the "
            "in-VM ttyd shell to the entire network with no auth. Name the specific "
            "trusted segment (e.g. 10.20.0.0/16), or leave it unset to keep ttyd "
            "firewalled."
        )
    return str(net)


# Fail-closed guard for _render_userdata: any C0/C1 control character
# (including newline) inside an interpolated value could splice arbitrary
# cloud-init YAML into the guest, so the render aborts instead.
_USERDATA_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _render_userdata(spec: CloneSpec, template_path: Path) -> str:
    """Render {{ KEY }} placeholders against the C3 ``user-data.yaml.tpl``.

    The template uses the generic ``AGENT_*`` keys (decision 2D). For each we
    prefer the new ``agent_*`` field and fall back to the deprecated ``goose_*``
    / ``owner_login`` / ``registry_url`` compat aliases when it is unset, so
    callers that have not yet migrated keep rendering correctly.

    Every value is checked against ``_USERDATA_UNSAFE`` before substitution —
    a newline or control character fails the clone (fail closed) rather than
    rendering injected YAML into guestinfo.userdata.
    """
    text = template_path.read_text()
    values = {
        "AGENT_KIND": spec.agent_kind,
        "AGENT_VERSION": spec.agent_version or spec.goose_image_tag,
        "AGENT_USER": spec.agent_user or spec.owner_login,
        "AGENT_USER_UID": str(spec.agent_user_uid),
        "AGENT_MODEL": spec.agent_model or spec.goose_model,
        "AGENT_REGISTRY_URL": spec.agent_registry_url or spec.registry_url,
        "AGENT_PLATFORM_USER_TOKEN": spec.user_token,
        "GATEWAY_URL": spec.litellm_gateway_url,
        "HEARTBEAT_URL": spec.heartbeat_url,
        # SEC-1: empty unless the deploy supplied a trusted CIDR; validated so a
        # malformed value fails the clone instead of corrupting install.env.
        "TTYD_ALLOW_CIDR": _validate_ttyd_allow_cidr(spec.ttyd_allow_cidr),
    }
    for key, val in values.items():
        if _USERDATA_UNSAFE.search(val):
            raise ValueError(
                f"cloud-init value for {key} contains a newline / control character — "
                f"refusing to render (YAML injection guard, fail closed)"
            )
        text = text.replace("{{ " + key + " }}", val)
    return text


def _resolve_destination(content, template):
    """Return (resourcePool, folder, datastore). vcsim-friendly defaults."""
    folder = template.parent if isinstance(template.parent, vim.Folder) else None
    if folder is None:
        folder = content.rootFolder.childEntity[0].vmFolder

    dc = content.rootFolder.childEntity[0]
    rp = None
    for child in dc.hostFolder.childEntity:
        if hasattr(child, "resourcePool"):
            rp = child.resourcePool
            break
    if rp is None:
        raise RuntimeError("no resourcePool reachable under datacenter")

    if not dc.datastoreFolder.childEntity:
        raise RuntimeError("no datastore reachable under datacenter")
    ds = dc.datastoreFolder.childEntity[0]

    return rp, folder, ds


def _wait_sync(task, timeout_s: float, poll_s: float = 0.5):
    elapsed = 0.0
    while elapsed < timeout_s:
        state = task.info.state
        if state == vim.TaskInfo.State.success:
            return task.info.result
        if state == vim.TaskInfo.State.error:
            err = task.info.error
            msg = getattr(err, "msg", None) or repr(err)
            raise RuntimeError(f"clone task failed: {msg}")
        time.sleep(poll_s)
        elapsed += poll_s
    raise TimeoutError(f"task did not finish within {timeout_s}s")


class VmwareProvisioner(Provisioner):
    """Real provisioner. Wraps pyVmomi against a vCenter or vcsim."""

    def __init__(
        self,
        vcenter_url: str,
        vcenter_user: str,
        vcenter_password: str,
        cloud_init_template_path: str | Path,
        verify_ssl: bool = True,
        clone_timeout_s: float = 600.0,
    ) -> None:
        parsed = urlparse(vcenter_url if "://" in vcenter_url else f"https://{vcenter_url}")
        if not parsed.hostname:
            raise ValueError(f"invalid vcenter_url: {vcenter_url}")
        self._target = _Target(
            host=parsed.hostname,
            port=parsed.port or 443,
            user=vcenter_user,
            password=vcenter_password,
            verify_ssl=verify_ssl,
        )
        self._template_path = Path(cloud_init_template_path)
        if not self._template_path.is_file():
            raise FileNotFoundError(f"cloud-init template not found: {self._template_path}")
        self._clone_timeout_s = clone_timeout_s

    async def clone_vm(self, spec: CloneSpec) -> CloneResult:
        try:
            return await asyncio.to_thread(self._clone_sync, spec)
        except Exception as exc:
            logger.exception("clone_vm failed for %s", spec.intended_name)
            # Sanitize: vCenter error text is untrusted — strip control chars +
            # truncate before it reaches logs / API responses / an LLM (#H-6).
            return CloneResult(success=False, error=sanitize(str(exc)))

    def _clone_sync(self, spec: CloneSpec) -> CloneResult:
        si = _connect(self._target)
        content = si.RetrieveContent()

        # Idempotency contract (protocol.py): if a VM with intended_name already
        # exists, return success. Protects against worker double-claim (#81 race)
        # and against safe-retry after a transient failure mid-clone.
        existing = _find_vm_by_name(content, spec.intended_name)
        if existing is not None:
            existing_ip = None
            with contextlib.suppress(Exception):
                existing_ip = existing.guest.ipAddress
            logger.info(
                "idempotent hit: VM %r already exists, returning success", spec.intended_name
            )
            return CloneResult(
                success=True,
                vm_id=str(existing._moId),
                ip_address=existing_ip,
            )

        template = _find_template(si, spec.template)
        if template is None:
            return CloneResult(
                success=False,
                error=(
                    f"template not found: '{spec.template}'. Use the full inventory "
                    f"path (e.g. '/DC0/vm/template-name') or a flat VM name."
                ),
            )

        rp, folder, ds = _resolve_destination(content, template)
        userdata = _render_userdata(spec, self._template_path)
        extra_config = _build_extra_config(userdata, spec.intended_name)

        relocate = vim.vm.RelocateSpec(pool=rp, datastore=ds)
        config_spec = vim.vm.ConfigSpec(extraConfig=extra_config)
        clone_spec = vim.vm.CloneSpec(
            location=relocate,
            powerOn=True,
            template=False,
            config=config_spec,
        )

        task = template.CloneVM_Task(folder=folder, name=spec.intended_name, spec=clone_spec)
        result_vm = _wait_sync(task, timeout_s=self._clone_timeout_s)

        ip = None
        with contextlib.suppress(Exception):
            ip = result_vm.guest.ipAddress

        return CloneResult(
            success=True,
            vm_id=str(result_vm._moId),
            ip_address=ip,
        )

    async def destroy_vm(self, vm_id: str) -> None:
        await asyncio.to_thread(self._destroy_sync, vm_id)

    def _destroy_sync(self, vm_id: str) -> None:
        """Power-off (if needed) + Destroy_Task, idempotent on missing VMs.

        Used by the decision-5 cleanup cron + decision-7 ``force=true``
        retry. Both paths treat "VM already gone" as success — the
        post-condition is "the VM is not in vCenter inventory", which
        a missing VM already satisfies.
        """
        si = _connect(self._target)
        content = si.RetrieveContent()
        # MoRef → ManagedObject by-id lookup. vSphere SDK exposes this via
        # the search index; missing VMs return None.
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        target = None
        try:
            for candidate in view.view:
                if str(candidate._moId) == vm_id:
                    target = candidate
                    break
        finally:
            view.Destroy()

        if target is None:
            logger.info("destroy_vm: VM %s already gone, treating as success", vm_id)
            return

        # Power-off first if running — DestroyVM on a powered-on VM raises.
        # Idempotent: PoweredOff stays PoweredOff.
        try:
            if target.runtime.powerState == vim.VirtualMachinePowerState.poweredOn:
                _wait_sync(target.PowerOffVM_Task(), timeout_s=self._clone_timeout_s)
        except vim.fault.InvalidPowerState:
            # Already in the requested state; safe to continue.
            pass

        _wait_sync(target.Destroy_Task(), timeout_s=self._clone_timeout_s)
        logger.info("destroy_vm: %s destroyed", vm_id)
