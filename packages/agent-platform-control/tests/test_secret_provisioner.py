"""Tests for orchestrator.secret_provisioner — per-VM secret CRUD glue (PR-C C-4).

Decision 4 lock 2026-05-29: every VM gets ``ssh_authorized_keys`` (from
the owner's portal-uploaded pubkey) and ``heartbeat_access_token`` (freshly
minted) written into the per-VM secret store at provisioning time. VM
retirement (decision 6 + 9) revokes both via ``revoke_per_vm_secrets``.

This module is **store-agnostic** — tests use ``InMemoryVmSecretStore``
from agent_platform_secrets, prod will pass a ``VaultwardenVmSecretStore``.
"""

from __future__ import annotations

import pytest
from agent_platform_control.orchestrator.secret_provisioner import (
    generate_heartbeat_token,
    provision_per_vm_secrets,
    revoke_per_vm_secrets,
)
from agent_platform_secrets.vm_secret_store import (
    InMemoryVmSecretStore,
    VmSecretRef,
)

# ---------------------------------------------------------- generate_heartbeat_token


class TestGenerateHeartbeatToken:
    def test_has_recognizable_prefix(self):
        assert generate_heartbeat_token().startswith("hbt_")

    def test_unique_per_call(self):
        seen = {generate_heartbeat_token() for _ in range(20)}
        assert len(seen) == 20

    def test_length_at_least_32(self):
        # 256-bit entropy → at least 32 base32 chars after the prefix
        token = generate_heartbeat_token()
        assert len(token) > 32


# ---------------------------------------------------------- provision_per_vm_secrets


@pytest.fixture
def store() -> InMemoryVmSecretStore:
    return InMemoryVmSecretStore()


class TestProvisionPerVmSecrets:
    @pytest.mark.asyncio
    async def test_writes_both_secrets_when_pubkey_present(self, store):
        pubkey = "ssh-ed25519 AAAA... alice@laptop"
        result = await provision_per_vm_secrets(
            store, deployment_item_id=42, owner_ssh_pubkey=pubkey
        )

        # ssh_authorized_keys = the pubkey verbatim
        assert (await store.get(VmSecretRef("vm", "42", "ssh_authorized_keys"))) == pubkey
        # heartbeat token persisted and returned for downstream wiring
        stored_hbt = await store.get(VmSecretRef("vm", "42", "heartbeat_access_token"))
        assert stored_hbt is not None
        assert stored_hbt.startswith("hbt_")
        assert result.heartbeat_token == stored_hbt

    @pytest.mark.asyncio
    async def test_writes_empty_ssh_when_pubkey_missing(self, store):
        """Decision 4: VM still boots if user never uploaded a key."""
        result = await provision_per_vm_secrets(store, deployment_item_id=42, owner_ssh_pubkey=None)

        assert (await store.get(VmSecretRef("vm", "42", "ssh_authorized_keys"))) == ""
        assert result.heartbeat_token.startswith("hbt_")

    @pytest.mark.asyncio
    async def test_idempotent_overwrite(self, store):
        """Re-provision (decision 7 force=true) updates secrets cleanly."""
        first = await provision_per_vm_secrets(
            store, deployment_item_id=42, owner_ssh_pubkey="ssh-ed25519 A"
        )
        second = await provision_per_vm_secrets(
            store, deployment_item_id=42, owner_ssh_pubkey="ssh-ed25519 B"
        )

        assert (await store.get(VmSecretRef("vm", "42", "ssh_authorized_keys"))) == "ssh-ed25519 B"
        # Heartbeat token rotates — old token is no longer valid
        assert first.heartbeat_token != second.heartbeat_token

    @pytest.mark.asyncio
    async def test_only_writes_per_vm_layer(self, store):
        """Sanity: provisioner never touches tenant-layer secrets."""
        await provision_per_vm_secrets(
            store, deployment_item_id=42, owner_ssh_pubkey="ssh-ed25519 X"
        )
        # No tenant-layer entries created
        refs = await store.list_vm_refs(deployment_item_id=42)
        assert all(r.layer == "vm" for r in refs)
        assert {r.key for r in refs} == {"ssh_authorized_keys", "heartbeat_access_token"}


# ---------------------------------------------------------- revoke_per_vm_secrets


class TestRevokePerVmSecrets:
    @pytest.mark.asyncio
    async def test_removes_both_provisioned_secrets(self, store):
        await provision_per_vm_secrets(
            store, deployment_item_id=42, owner_ssh_pubkey="ssh-ed25519 X"
        )
        await revoke_per_vm_secrets(store, deployment_item_id=42)

        assert await store.get(VmSecretRef("vm", "42", "ssh_authorized_keys")) is None
        assert await store.get(VmSecretRef("vm", "42", "heartbeat_access_token")) is None

    @pytest.mark.asyncio
    async def test_revoke_idempotent(self, store):
        """Retiring a VM that was never provisioned is a no-op, not an error."""
        await revoke_per_vm_secrets(store, deployment_item_id=999)

    @pytest.mark.asyncio
    async def test_revoke_doesnt_touch_other_vms(self, store):
        await provision_per_vm_secrets(
            store, deployment_item_id=42, owner_ssh_pubkey="ssh-ed25519 A"
        )
        await provision_per_vm_secrets(
            store, deployment_item_id=99, owner_ssh_pubkey="ssh-ed25519 B"
        )
        await revoke_per_vm_secrets(store, deployment_item_id=42)

        # 99's secrets are intact
        assert (await store.get(VmSecretRef("vm", "99", "ssh_authorized_keys"))) == "ssh-ed25519 B"
        assert (await store.get(VmSecretRef("vm", "99", "heartbeat_access_token"))) is not None


def test_hash_heartbeat_token_matches_sha256_hex():
    """#350: the digest stamped on DeploymentItem.heartbeat_token_hash must be
    a stable sha256 hex so /api/heartbeat's boundary hash agrees with it."""
    import hashlib

    from agent_platform_control.orchestrator.secret_provisioner import hash_heartbeat_token

    token = generate_heartbeat_token()
    assert hash_heartbeat_token(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()
    # Deterministic + 64 hex chars.
    assert hash_heartbeat_token(token) == hash_heartbeat_token(token)
    assert len(hash_heartbeat_token(token)) == 64
