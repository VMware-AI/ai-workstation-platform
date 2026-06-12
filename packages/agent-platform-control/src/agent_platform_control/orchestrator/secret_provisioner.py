"""Per-VM secret provisioning + revocation (PR-C, decisions 4 + 6 + 9).

Glue between the deployment flow (worker / from-approval / future heartbeat
re-issue) and the runtime :class:`VmSecretStore`. Keeps secret-handling
logic out of the API handlers so any deployment-creating path can call
:func:`provision_per_vm_secrets` without duplicating logic.

What lives here:

* :func:`generate_heartbeat_token` — short opaque token for the in-VM
  heartbeat to authenticate against ``POST /api/heartbeat`` (decision 16,
  endpoint lands in PR-D).
* :func:`provision_per_vm_secrets` — write the 2 per-VM secrets at clone
  time. Idempotent so retry / decision-7 force=true works cleanly.
* :func:`revoke_per_vm_secrets` — wipe per-VM entries when a VM is retired
  (decision 6). Idempotent so cleanup crons don't blow up on already-clean
  VMs.

What does NOT live here (separation of concerns):

* The bootstrap user_token / Fernet encryption (``tokens.py``) — that is a
  single-use cloud-init credential, life-cycled with the deployment_item
  row itself, not with the runtime secret store.
* Per-tenant secrets (LLM key, registry creds) — admin-managed in
  Vaultwarden; provisioning never writes them.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

from agent_platform_secrets.vm_secret_store import VmSecretRef, VmSecretStore

# Per-VM secret keys (decision 4 lock 2026-05-29). Centralised here so
# every call site agrees on the string.
SSH_AUTHORIZED_KEYS = "ssh_authorized_keys"
HEARTBEAT_ACCESS_TOKEN = "heartbeat_access_token"  # noqa: S105 — key NAME, not a value  # nosec B105 — key NAME, not a value

# 256 bits of entropy → 52 base32 chars, fits comfortably below the
# Vaultwarden notes field limit. Prefix lets log greps tell heartbeat
# tokens apart from bootstrap user_tokens ("tok_") at a glance.
_HEARTBEAT_BYTES = 32


@dataclass(frozen=True)
class ProvisioningResult:
    """Return value from :func:`provision_per_vm_secrets`.

    Heartbeat token is returned so the caller can stash it in any other
    sink that needs it (e.g. the deployment_items row, audit log).
    """

    heartbeat_token: str


def generate_heartbeat_token() -> str:
    """Random opaque heartbeat bearer (``hbt_`` + base32, ~256 bits)."""
    raw = secrets.token_bytes(_HEARTBEAT_BYTES)
    body = base64.b32encode(raw).rstrip(b"=").decode("ascii").lower()
    return f"hbt_{body}"


def hash_heartbeat_token(token: str) -> str:
    """sha256 hex of a heartbeat bearer.

    Colocated with generation so the value written to
    ``DeploymentItem.heartbeat_token_hash`` (worker, PR-D wiring) and the
    value looked up by ``POST /api/heartbeat`` use one algorithm — they
    must agree or every heartbeat 401s. Plaintext is never persisted; only
    this digest reaches the DB.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def provision_per_vm_secrets(
    store: VmSecretStore,
    *,
    deployment_item_id: int,
    owner_ssh_pubkey: str | None,
) -> ProvisioningResult:
    """Write per-VM secrets for a new (or re-provisioned) deployment item.

    Idempotent: re-calling with the same ``deployment_item_id`` overwrites
    both secrets. Heartbeat token rotates on every call — old tokens
    become invalid.

    Empty / missing ``owner_ssh_pubkey`` writes an empty string for
    ``ssh_authorized_keys`` rather than skipping. cloud-init then writes
    an empty ``authorized_keys`` file and the VM boots; the user can
    SSH in only after uploading a pubkey via the portal.
    """
    scope = str(deployment_item_id)
    heartbeat = generate_heartbeat_token()

    await store.put(
        VmSecretRef("vm", scope, SSH_AUTHORIZED_KEYS),
        owner_ssh_pubkey or "",
    )
    await store.put(
        VmSecretRef("vm", scope, HEARTBEAT_ACCESS_TOKEN),
        heartbeat,
    )
    return ProvisioningResult(heartbeat_token=heartbeat)


async def revoke_per_vm_secrets(
    store: VmSecretStore,
    *,
    deployment_item_id: int,
) -> None:
    """Wipe every per-VM entry for the given deployment item.

    Idempotent: revoking a never-provisioned VM is a no-op. Used by the
    decision 6 retirement path + the decision 5 failed-VM cleanup cron.
    """
    refs = await store.list_vm_refs(deployment_item_id=deployment_item_id)
    for ref in refs:
        await store.delete(ref)
