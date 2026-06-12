"""Runtime VM secret store (PR-C, decision 4 + 9 locked 2026-05-29).

**Different from `resolver.py`**: that module resolves config-layer
references like ``vault://nsx/admin-password`` to plaintext. This module
manages the CRUD lifecycle of the 4 M1 secrets that flow between the C1
control plane, the C18 Vaultwarden, and the in-VM cloud-init plugin.

Naming (locked, decision 9):

* ``tenant/{tenant_id}/{key}``  → per-tenant collection (shared across
  every VM in that tenant)
* ``vm/{deployment_item_id}/{key}`` → per-VM collection (lives in the
  ``vm-ephemeral`` Vaultwarden collection; cleaned when the VM is retired)

The 4 M1 secret keys (decision 4):

* per-tenant: ``llm_gateway_api_key``, ``registry_pull_creds``
* per-VM:     ``ssh_authorized_keys``, ``heartbeat_access_token``

M2 will add ``git_credentials`` (per-VM) and ``selfupdate_channel_key``
(per-tenant); the store API is intentionally agnostic to the key name so
new entries are zero-code adds.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol

Layer = Literal["tenant", "vm"]
_LAYERS: frozenset[str] = frozenset({"tenant", "vm"})


class VmSecretStoreError(ValueError):
    """Raised on invalid refs or store-level failures."""


@dataclass(frozen=True, slots=True)
class VmSecretRef:
    """Typed reference into the runtime VM secret store.

    Frozen dataclass so refs are hashable (used as dict keys in
    :class:`InMemoryVmSecretStore`). Validates inputs in ``__post_init__``
    so callers cannot construct a ref with an unknown layer or a key that
    contains the path separator.
    """

    layer: Layer
    scope_id: str  # tenant_id (for layer="tenant") or str(deployment_item_id)
    key: str

    def __post_init__(self) -> None:
        if self.layer not in _LAYERS:
            raise VmSecretStoreError(
                f"unknown layer {self.layer!r}; expected one of {sorted(_LAYERS)}"
            )
        if not self.scope_id:
            raise VmSecretStoreError("scope_id is empty")
        if "/" in self.scope_id:
            raise VmSecretStoreError(
                f"scope_id must not contain '/' (got {self.scope_id!r}); "
                "reserved as the path separator so flat() round-trips"
            )
        if not self.key:
            raise VmSecretStoreError("key is empty")
        if "/" in self.key:
            raise VmSecretStoreError(
                f"key must not contain '/' (got {self.key!r}); reserved as the path separator"
            )

    def flat(self) -> str:
        """Round-trippable flat string used for logs / debugging."""
        return f"{self.layer}/{self.scope_id}/{self.key}"


def parse_vm_secret_ref(raw: str) -> VmSecretRef:
    """Inverse of :meth:`VmSecretRef.flat`. Raises on malformed input."""
    parts = raw.split("/")
    if len(parts) != 3:
        raise VmSecretStoreError(
            f"expected 'layer/scope_id/key' (3 parts), got {len(parts)}: {raw!r}"
        )
    layer, scope_id, key = parts
    return VmSecretRef(layer=layer, scope_id=scope_id, key=key)


class VmSecretStore(Protocol):
    """CRUD API for the 4 M1 runtime secrets.

    Implementations:

    * :class:`InMemoryVmSecretStore` — tests + M1 stub
    * ``VaultwardenVmSecretStore``    — production backend (separate PR;
      see ``vaultwarden_client.py`` for the underlying API client)
    """

    async def get(self, ref: VmSecretRef) -> str | None:
        """Return plaintext value, or ``None`` if the ref is unset.

        Callers decide whether ``None`` is an error or a fallback (e.g.
        SSH key is allowed to be absent — VM still boots, user just can't
        SSH in until they upload one).
        """
        ...

    async def put(self, ref: VmSecretRef, value: str) -> None:
        """Set the value. Idempotent — overwrites any existing value."""
        ...

    async def delete(self, ref: VmSecretRef) -> None:
        """Remove the ref. Idempotent — deleting a missing ref is a no-op."""
        ...

    async def list_vm_refs(self, deployment_item_id: int) -> list[VmSecretRef]:
        """All refs in the ``vm/{deployment_item_id}/*`` namespace.

        Used by the VM-retirement path (decision 6 + 9) to wipe per-VM
        secrets when the VM is decommissioned. Tenant-scoped refs are
        deliberately excluded.
        """
        ...


# ----------------------------------------------------------- InMemory


class InMemoryVmSecretStore:
    """Dict-backed implementation for tests + the M1 stub path.

    M1 plan §4 puts this behind ``C1 control`` so the cloud-init exchange
    endpoint already works end-to-end without Vaultwarden wired. The
    VaultwardenVmSecretStore swap is a single-line change in the C1
    factory (planned for a follow-up after Vaultwarden's bring-up is
    confirmed in the customer environment).
    """

    def __init__(self) -> None:
        self._data: dict[VmSecretRef, str] = {}
        # asyncio.Lock keeps put/delete linear so a parallel write doesn't
        # interleave with a list_vm_refs snapshot. In-process only.
        self._lock = asyncio.Lock()

    async def get(self, ref: VmSecretRef) -> str | None:
        return self._data.get(ref)

    async def put(self, ref: VmSecretRef, value: str) -> None:
        async with self._lock:
            self._data[ref] = value

    async def delete(self, ref: VmSecretRef) -> None:
        async with self._lock:
            self._data.pop(ref, None)

    async def list_vm_refs(self, deployment_item_id: int) -> list[VmSecretRef]:
        target = str(deployment_item_id)
        async with self._lock:
            return [ref for ref in self._data if ref.layer == "vm" and ref.scope_id == target]
