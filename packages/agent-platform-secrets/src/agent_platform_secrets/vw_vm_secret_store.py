"""Vaultwarden-backed VmSecretStore (PR-G G-2).

Bridges the per-VM ephemeral secret API expected by C1's
``secret_provisioner`` to a real Vaultwarden deployment, replacing
the in-memory dict used in tests + dev.

Naming convention follows OQ-5 (flat, 2-layer):
  * ``tenant-shared/<name>``        — shared per-tenant
  * ``vm-ephemeral/<vm_id>/<name>`` — per-VM, revoked on VM teardown

Failure mode is **fail-closed** (decision §5 of 16-a-vaultwarden-binding-plan):
a transient Vaultwarden outage surfaces as :class:`VmSecretStoreError`,
and the caller (C1 deploy worker) marks the deployment as ``failed``
with a teaching error message — never silently falls back to in-memory.

PR-C #135 hasn't merged yet, so this file ships its own local
``VmSecretRef`` + ``VmSecretStore`` Protocol. The follow-up after both
PR-C and PR-G land swaps the local symbols for
``from .vm_secret_store import VmSecretRef, VmSecretStore`` (~5 lines).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .vaultwarden_client import (
    VaultwardenAuthError,
    VaultwardenClient,
    VaultwardenError,
    VaultwardenNotFoundError,
)

logger = logging.getLogger(__name__)


class VmSecretStoreError(RuntimeError):
    """Raised when the underlying store can't satisfy a request.

    Callers (C1's secret_provisioner) treat this as a hard failure:
    deployment moves to ``failed`` with the message surfaced in the
    user-visible status field.
    """


# ---------------------------------------------------------------- local PR-C surface


@dataclass(frozen=True, slots=True)
class VmSecretRef:
    """Locator for one per-VM or per-tenant secret.

    PR-G TODO: replace with ``from .vm_secret_store import VmSecretRef``
    after PR-C #135 + PR-G both merge.
    """

    namespace: str  # "tenant-shared" | "vm-ephemeral"
    scope: str  # tenant_id or vm_id
    name: str

    def __post_init__(self) -> None:
        if self.namespace not in ("tenant-shared", "vm-ephemeral"):
            raise ValueError(f"unknown namespace {self.namespace!r}")
        if not self.scope or "/" in self.scope:
            raise ValueError(f"scope must be non-empty and slash-free, got {self.scope!r}")
        if not self.name or "/" in self.name:
            raise ValueError(f"name must be non-empty and slash-free, got {self.name!r}")

    @property
    def folder(self) -> str:
        """Folder path inside the secret store, per OQ-5 flat layout."""
        return f"{self.namespace}/{self.scope}"


@runtime_checkable
class VmSecretStore(Protocol):
    """Protocol C1's secret_provisioner depends on.

    PR-G TODO: drop this local Protocol after PR-C #135 merges; replace
    with import from ``.vm_secret_store``.
    """

    def get(self, ref: VmSecretRef) -> str: ...
    def put(self, ref: VmSecretRef, value: str) -> None: ...
    def delete(self, ref: VmSecretRef) -> None: ...
    def list_vm_refs(self, vm_id: str) -> list[VmSecretRef]: ...


# ---------------------------------------------------------------- adapter


@dataclass
class VaultwardenVmSecretStore:
    """Implements :class:`VmSecretStore` against a live Vaultwarden REST API.

    Construct with an already-configured :class:`VaultwardenClient`.
    Failure mode: any underlying ``VaultwardenError`` surfaces as
    :class:`VmSecretStoreError` with a teaching message.
    """

    client: VaultwardenClient

    def get(self, ref: VmSecretRef) -> str:
        try:
            return self.client.fetch_password(folder=ref.folder, name=ref.name)
        except VaultwardenNotFoundError as exc:
            raise VmSecretStoreError(
                f"secret {ref.name!r} missing in {ref.folder!r}. "
                "Check that secret_provisioner ran before this deployment."
            ) from exc
        except VaultwardenAuthError as exc:
            raise VmSecretStoreError(
                f"Vaultwarden auth failed reading {ref.folder}/{ref.name}: {exc}. "
                "Check AGENT_PLATFORM_VW_CLIENT_ID / AGENT_PLATFORM_VW_CLIENT_SECRET."
            ) from exc
        except VaultwardenError as exc:
            raise VmSecretStoreError(
                f"Vaultwarden unreachable reading {ref.folder}/{ref.name}: {exc}. "
                "Check AGENT_PLATFORM_VW_BASE_URL + network."
            ) from exc

    def put(self, ref: VmSecretRef, value: str) -> None:
        try:
            self.client.put_password(folder=ref.folder, name=ref.name, value=value)
        except VaultwardenAuthError as exc:
            raise VmSecretStoreError(
                f"Vaultwarden auth failed writing {ref.folder}/{ref.name}: {exc}"
            ) from exc
        except VaultwardenError as exc:
            raise VmSecretStoreError(
                f"Vaultwarden write failed for {ref.folder}/{ref.name}: {exc}"
            ) from exc

    def delete(self, ref: VmSecretRef) -> None:
        try:
            self.client.delete_password(folder=ref.folder, name=ref.name)
        except VaultwardenAuthError as exc:
            raise VmSecretStoreError(
                f"Vaultwarden auth failed deleting {ref.folder}/{ref.name}: {exc}"
            ) from exc
        except VaultwardenError as exc:
            raise VmSecretStoreError(
                f"Vaultwarden delete failed for {ref.folder}/{ref.name}: {exc}"
            ) from exc

    def list_vm_refs(self, vm_id: str) -> list[VmSecretRef]:
        folder = f"vm-ephemeral/{vm_id}"
        try:
            names = self.client.list_folder(folder)
        except VaultwardenError as exc:
            raise VmSecretStoreError(f"Vaultwarden list failed for {folder}: {exc}") from exc
        return [VmSecretRef(namespace="vm-ephemeral", scope=vm_id, name=n) for n in names]
