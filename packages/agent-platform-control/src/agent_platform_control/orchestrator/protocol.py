"""Provisioner Protocol — what the worker calls.

Concrete impls:
  - FakeProvisioner (fake.py): deterministic outcomes for tests
  - VmwareProvisioner (vmware.py): real pyVmomi against vCenter / vcsim

The Protocol intentionally returns dataclasses (frozen) — callers shouldn't
mutate provisioning state through the result object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CloneSpec:
    """Single-VM clone request handed to the provisioner.

    Naming note (PR-A 2026-05-29): generic ``agent_*`` fields are the canonical
    ones going forward (decision 2D — agent plugin model). The original
    ``goose_*`` fields are kept as a backwards-compat shim during the PR-A
    migration window; the worker writes both and provisioners read whichever
    they need. A follow-up PR drops the ``goose_*`` names once all callers are
    migrated.
    """

    intended_name: str
    template: str  # vCenter template inventory path
    tenant_id: str
    owner_id: str  # business owner from users table
    owner_login: str  # DEPRECATED alias of agent_user; kept for compat
    image_version: str
    # OVF properties injected into cloud-init by the provisioner:
    registry_url: str
    goose_image_tag: str  # DEPRECATED alias of agent_version
    litellm_gateway_url: str
    user_token: str
    heartbeat_url: str
    goose_model: str = "qwen-coder-32b"  # DEPRECATED alias of agent_model
    # --- new generic fields (decision 1B + 2D) ---
    agent_kind: str = "goose"  # "goose" | "qoder" | "xiaoguai" | ...
    agent_version: str = ""  # plugin-specific (docker tag, tarball ver)
    agent_user: str = ""  # sanitized linux account name (1B)
    agent_user_uid: int = 0  # derived UID in [1000, 50999] (1B)
    agent_model: str = ""  # LLM model selection, generic
    agent_registry_url: str = ""  # plugin-specific binary/image mirror
    # SEC-1: trusted-network CIDR install-agent.sh opens ttyd:7681 to. Empty by
    # default — the in-VM script then fails closed and leaves 7681 firewalled
    # rather than exposing an unauthenticated shell. Until the M2 W-3.3 HMAC
    # sidecar lands, only a deploy that explicitly supplies a CIDR gets a
    # reachable Terminal page.
    ttyd_allow_cidr: str = ""


@dataclass(frozen=True)
class CloneResult:
    """Outcome of a single clone attempt."""

    success: bool
    vm_id: str | None = None  # vCenter MoRef or our UUID once known
    ip_address: str | None = None
    error: str | None = None  # populated when success is False


class Provisioner(Protocol):
    """Async provisioner interface — the worker calls one method per item."""

    async def clone_vm(self, spec: CloneSpec) -> CloneResult:
        """Clone + customize + power-on one VM.

        Must be reentrant: worker may retry the same spec after a crash. Impls
        should treat an existing VM with the intended_name as success (idempotent).
        Returns CloneResult; exceptions escape only for programmer errors.
        """
        ...

    async def destroy_vm(self, vm_id: str) -> None:
        """Decommission a VM by vCenter MoRef / instance id.

        Used by the decision-5 cleanup cron + decision-7 ``force=true`` retry.
        Idempotent: a missing VM is a success (the post-condition "the VM is
        gone" already holds). Errors escape only for unexpected failures
        (auth, network, vCenter outage).
        """
        ...
