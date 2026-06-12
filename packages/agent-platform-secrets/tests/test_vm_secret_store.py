"""Tests for the runtime VM secret store (PR-C, decision 4 + 9).

Distinct from the config-layer SecretResolver (resolver.py) — this module is
about CRUD-managing the 4 M1 secrets per VM / per tenant. The naming + tests
mirror decisions 4 and 9 as locked on 2026-05-29.
"""

from __future__ import annotations

import pytest
from agent_platform_secrets.vm_secret_store import (
    InMemoryVmSecretStore,
    VmSecretRef,
    VmSecretStoreError,
    parse_vm_secret_ref,
)

# ---------------------------------------------------------------- VmSecretRef


class TestVmSecretRef:
    def test_construct_tenant_ref(self):
        ref = VmSecretRef(layer="tenant", scope_id="t-a", key="llm_gateway_api_key")
        assert ref.layer == "tenant"

    def test_construct_vm_ref(self):
        ref = VmSecretRef(layer="vm", scope_id="42", key="ssh_authorized_keys")
        assert ref.layer == "vm"

    def test_rejects_unknown_layer(self):
        with pytest.raises(VmSecretStoreError, match="layer"):
            VmSecretRef(layer="agent", scope_id="x", key="y")

    def test_rejects_empty_scope_id(self):
        with pytest.raises(VmSecretStoreError):
            VmSecretRef(layer="tenant", scope_id="", key="k")

    def test_rejects_empty_key(self):
        with pytest.raises(VmSecretStoreError):
            VmSecretRef(layer="vm", scope_id="42", key="")

    def test_rejects_slash_in_scope_id(self):
        # A "/" inside scope_id breaks the flat() round-trip:
        # "vm/a/b/key" parses as 4 parts, not the original ref.
        with pytest.raises(VmSecretStoreError, match="scope_id"):
            VmSecretRef(layer="vm", scope_id="a/b", key="ssh_authorized_keys")

    def test_flat_form_round_trips(self):
        for ref in [
            VmSecretRef("tenant", "t-a", "llm_gateway_api_key"),
            VmSecretRef("vm", "42", "ssh_authorized_keys"),
        ]:
            assert parse_vm_secret_ref(ref.flat()) == ref

    def test_flat_form_tenant_naming(self):
        ref = VmSecretRef("tenant", "t-a", "llm_gateway_api_key")
        assert ref.flat() == "tenant/t-a/llm_gateway_api_key"

    def test_flat_form_vm_naming(self):
        ref = VmSecretRef("vm", "42", "ssh_authorized_keys")
        assert ref.flat() == "vm/42/ssh_authorized_keys"


# ---------------------------------------------------------------- parse_vm_secret_ref


class TestParseVmSecretRef:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("tenant/t-a/llm_gateway_api_key", VmSecretRef("tenant", "t-a", "llm_gateway_api_key")),
            ("vm/42/ssh_authorized_keys", VmSecretRef("vm", "42", "ssh_authorized_keys")),
            (
                "vm/9999/heartbeat_access_token",
                VmSecretRef("vm", "9999", "heartbeat_access_token"),
            ),
        ],
    )
    def test_happy_path(self, raw, expected):
        assert parse_vm_secret_ref(raw) == expected

    def test_unknown_layer_prefix(self):
        with pytest.raises(VmSecretStoreError, match="layer"):
            parse_vm_secret_ref("agent/x/y")

    def test_too_few_parts(self):
        with pytest.raises(VmSecretStoreError):
            parse_vm_secret_ref("tenant/t-a")

    def test_too_many_parts(self):
        # key itself cannot contain slashes — slash is reserved
        with pytest.raises(VmSecretStoreError):
            parse_vm_secret_ref("vm/42/foo/bar")


# ---------------------------------------------------------------- InMemoryVmSecretStore


@pytest.fixture
def store() -> InMemoryVmSecretStore:
    return InMemoryVmSecretStore()


class TestInMemoryStore:
    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: InMemoryVmSecretStore):
        ref = VmSecretRef("tenant", "t-a", "llm_gateway_api_key")
        assert await store.get(ref) is None

    @pytest.mark.asyncio
    async def test_put_then_get(self, store: InMemoryVmSecretStore):
        ref = VmSecretRef("tenant", "t-a", "llm_gateway_api_key")
        await store.put(ref, "sk-test-123")
        assert await store.get(ref) == "sk-test-123"

    @pytest.mark.asyncio
    async def test_put_overwrites(self, store: InMemoryVmSecretStore):
        ref = VmSecretRef("tenant", "t-a", "llm_gateway_api_key")
        await store.put(ref, "v1")
        await store.put(ref, "v2")
        assert await store.get(ref) == "v2"

    @pytest.mark.asyncio
    async def test_delete(self, store: InMemoryVmSecretStore):
        ref = VmSecretRef("vm", "42", "ssh_authorized_keys")
        await store.put(ref, "ssh-ed25519 AAAA...")
        await store.delete(ref)
        assert await store.get(ref) is None

    @pytest.mark.asyncio
    async def test_delete_idempotent(self, store: InMemoryVmSecretStore):
        # deleting a non-existent ref should not raise
        await store.delete(VmSecretRef("vm", "999", "ssh_authorized_keys"))

    @pytest.mark.asyncio
    async def test_list_vm_refs_returns_only_one_vm(self, store: InMemoryVmSecretStore):
        await store.put(VmSecretRef("vm", "42", "ssh_authorized_keys"), "k1")
        await store.put(VmSecretRef("vm", "42", "heartbeat_access_token"), "t1")
        await store.put(VmSecretRef("vm", "99", "ssh_authorized_keys"), "k2")
        await store.put(VmSecretRef("tenant", "t-a", "llm_gateway_api_key"), "sk")

        refs_42 = await store.list_vm_refs(deployment_item_id=42)
        assert {r.key for r in refs_42} == {"ssh_authorized_keys", "heartbeat_access_token"}
        for r in refs_42:
            assert r.layer == "vm"
            assert r.scope_id == "42"

    @pytest.mark.asyncio
    async def test_list_vm_refs_excludes_tenant(self, store: InMemoryVmSecretStore):
        await store.put(VmSecretRef("tenant", "t-a", "llm_gateway_api_key"), "sk")
        refs = await store.list_vm_refs(deployment_item_id=42)
        assert refs == []

    @pytest.mark.asyncio
    async def test_round_trip_all_4_m1_secrets(self, store: InMemoryVmSecretStore):
        """Decision 4 lock: M1 stores exactly these 4."""
        await store.put(VmSecretRef("tenant", "t-a", "llm_gateway_api_key"), "sk-A")
        await store.put(VmSecretRef("tenant", "t-a", "registry_pull_creds"), '{"u":"x","p":"y"}')
        await store.put(VmSecretRef("vm", "42", "ssh_authorized_keys"), "ssh-ed25519 ...")
        await store.put(VmSecretRef("vm", "42", "heartbeat_access_token"), "hbt_...")

        assert await store.get(VmSecretRef("tenant", "t-a", "llm_gateway_api_key")) == "sk-A"
        assert (
            await store.get(VmSecretRef("tenant", "t-a", "registry_pull_creds"))
            == '{"u":"x","p":"y"}'
        )
        assert await store.get(VmSecretRef("vm", "42", "ssh_authorized_keys")) == "ssh-ed25519 ..."
        assert await store.get(VmSecretRef("vm", "42", "heartbeat_access_token")) == "hbt_..."
