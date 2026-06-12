"""VaultwardenVmSecretStore adapter tests (PR-G G-2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx
from agent_platform_secrets.vaultwarden_client import (
    VaultwardenAuthError,
    VaultwardenClient,
    VaultwardenError,
    VaultwardenNotFoundError,
)
from agent_platform_secrets.vw_vm_secret_store import (
    VaultwardenVmSecretStore,
    VmSecretRef,
    VmSecretStore,
    VmSecretStoreError,
)

# --------------------------------------------------------------- VmSecretRef


def test_vm_secret_ref_validates_namespace():
    with pytest.raises(ValueError, match="unknown namespace"):
        VmSecretRef(namespace="bad", scope="vm-1", name="tok")


def test_vm_secret_ref_validates_scope():
    with pytest.raises(ValueError, match="scope"):
        VmSecretRef(namespace="vm-ephemeral", scope="", name="tok")
    with pytest.raises(ValueError, match="scope"):
        VmSecretRef(namespace="vm-ephemeral", scope="has/slash", name="tok")


def test_vm_secret_ref_validates_name():
    with pytest.raises(ValueError, match="name"):
        VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="")


def test_vm_secret_ref_folder_property():
    ref = VmSecretRef(namespace="vm-ephemeral", scope="vm-42", name="hbt")
    assert ref.folder == "vm-ephemeral/vm-42"

    shared = VmSecretRef(namespace="tenant-shared", scope="acme", name="api-key")
    assert shared.folder == "tenant-shared/acme"


def test_vm_secret_ref_is_frozen():
    ref = VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok")
    # dataclass(frozen=True) raises FrozenInstanceError on assignment.
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        ref.name = "other"  # type: ignore[misc]


# ------------------------------------------------------------------ Protocol


def test_store_implements_protocol():
    """Runtime check: adapter satisfies the VmSecretStore Protocol."""
    store = VaultwardenVmSecretStore(client=MagicMock())
    assert isinstance(store, VmSecretStore)


# -------------------------------------------------------------------- get


def test_get_returns_value_from_client():
    client = MagicMock()
    client.fetch_password.return_value = "hbt_secret"
    store = VaultwardenVmSecretStore(client=client)

    result = store.get(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"))

    assert result == "hbt_secret"
    client.fetch_password.assert_called_once_with(folder="vm-ephemeral/vm-1", name="tok")


def test_get_translates_not_found_to_teaching_error():
    client = MagicMock()
    client.fetch_password.side_effect = VaultwardenNotFoundError("missing")
    store = VaultwardenVmSecretStore(client=client)

    with pytest.raises(VmSecretStoreError, match="secret_provisioner ran"):
        store.get(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"))


def test_get_translates_auth_failure():
    client = MagicMock()
    client.fetch_password.side_effect = VaultwardenAuthError("401")
    store = VaultwardenVmSecretStore(client=client)

    with pytest.raises(VmSecretStoreError, match="AGENT_PLATFORM_VW_CLIENT"):
        store.get(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"))


def test_get_translates_transport_error():
    client = MagicMock()
    client.fetch_password.side_effect = VaultwardenError("network down")
    store = VaultwardenVmSecretStore(client=client)

    with pytest.raises(VmSecretStoreError, match="AGENT_PLATFORM_VW_BASE_URL"):
        store.get(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"))


# -------------------------------------------------------------------- put


def test_put_writes_via_client():
    client = MagicMock()
    client.put_password.return_value = "cipher-id"
    store = VaultwardenVmSecretStore(client=client)

    store.put(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"), "v")

    client.put_password.assert_called_once_with(folder="vm-ephemeral/vm-1", name="tok", value="v")


def test_put_translates_failures():
    client = MagicMock()
    client.put_password.side_effect = VaultwardenError("write rejected")
    store = VaultwardenVmSecretStore(client=client)

    with pytest.raises(VmSecretStoreError, match="write failed"):
        store.put(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"), "v")


@respx.mock
def test_put_translates_httpx_connect_error_end_to_end():
    """Real client + dead network: httpx.ConnectError must surface as
    VmSecretStoreError (via VaultwardenError), never raw — fail-closed
    contract from the module docstring."""
    respx.post("https://vw.test/identity/connect/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "fake-jwt", "expires_in": 3600, "token_type": "Bearer"}
        )
    )
    respx.get("https://vw.test/api/sync").mock(side_effect=httpx.ConnectError("refused"))
    store = VaultwardenVmSecretStore(
        client=VaultwardenClient(base_url="https://vw.test", client_id="c", client_secret="s")
    )
    with pytest.raises(VmSecretStoreError):
        store.put(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"), "v")


# ------------------------------------------------------------------ delete


def test_delete_calls_client():
    client = MagicMock()
    store = VaultwardenVmSecretStore(client=client)

    store.delete(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"))

    client.delete_password.assert_called_once_with(folder="vm-ephemeral/vm-1", name="tok")


def test_delete_translates_failures():
    client = MagicMock()
    client.delete_password.side_effect = VaultwardenError("nope")
    store = VaultwardenVmSecretStore(client=client)

    with pytest.raises(VmSecretStoreError, match="delete failed"):
        store.delete(VmSecretRef(namespace="vm-ephemeral", scope="vm-1", name="tok"))


# -------------------------------------------------------------- list_vm_refs


def test_list_vm_refs_returns_typed_refs():
    client = MagicMock()
    client.list_folder.return_value = ["tok-a", "tok-b"]
    store = VaultwardenVmSecretStore(client=client)

    refs = store.list_vm_refs("vm-7")

    assert refs == [
        VmSecretRef(namespace="vm-ephemeral", scope="vm-7", name="tok-a"),
        VmSecretRef(namespace="vm-ephemeral", scope="vm-7", name="tok-b"),
    ]
    client.list_folder.assert_called_once_with("vm-ephemeral/vm-7")


def test_list_vm_refs_empty_when_folder_absent():
    client = MagicMock()
    client.list_folder.return_value = []
    store = VaultwardenVmSecretStore(client=client)

    assert store.list_vm_refs("vm-never-deployed") == []


def test_list_vm_refs_translates_transport_error():
    client = MagicMock()
    client.list_folder.side_effect = VaultwardenError("conn refused")
    store = VaultwardenVmSecretStore(client=client)

    with pytest.raises(VmSecretStoreError, match="list failed"):
        store.list_vm_refs("vm-1")
