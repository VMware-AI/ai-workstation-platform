"""Write-side tests for VaultwardenClient (PR-G G-2).

Adds coverage for put_password / delete_password / list_folder.
Existing read tests live in test_vaultwarden_client.py.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from agent_platform_secrets import (
    VaultwardenAuthError,
    VaultwardenClient,
    VaultwardenError,
)


def _identity_ok() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "fake-jwt", "expires_in": 3600, "token_type": "Bearer"}
    )


def _empty_sync(folder_name: str | None = None, folder_id: str | None = None) -> httpx.Response:
    folders = []
    if folder_name is not None:
        folders = [{"Id": folder_id or "f-1", "Name": folder_name}]
    return httpx.Response(200, json={"Folders": folders, "Ciphers": []})


@pytest.fixture
def client():
    return VaultwardenClient(base_url="https://vw.test", client_id="cid", client_secret="csec")


# ----------------------------------------------------------------- put_password


@respx.mock
def test_put_password_creates_new_cipher_when_absent(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(
        return_value=_empty_sync(folder_name="vm-ephemeral/vm-1", folder_id="f-1")
    )
    captured: dict[str, object] = {}

    def _capture_post(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"Id": "new-cipher", "Name": captured["body"]["name"]})

    respx.post("https://vw.test/api/ciphers").mock(side_effect=_capture_post)

    result = client.put_password(
        folder="vm-ephemeral/vm-1", name="heartbeat_access_token", value="hbt_abc"
    )

    assert result == "new-cipher"
    body = captured["body"]
    assert body["name"] == "heartbeat_access_token"
    assert body["folderId"] == "f-1"
    assert body["type"] == 1  # Bitwarden login type
    assert body["login"]["password"] == "hbt_abc"


@respx.mock
def test_put_password_updates_existing_cipher(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "Folders": [{"Id": "f-1", "Name": "vm-ephemeral/vm-1"}],
                "Ciphers": [
                    {
                        "Id": "existing-cipher",
                        "Name": "heartbeat_access_token",
                        "FolderId": "f-1",
                        "Login": {"Password": "old"},
                    }
                ],
            },
        )
    )
    respx.put("https://vw.test/api/ciphers/existing-cipher").mock(
        return_value=httpx.Response(200, json={"Id": "existing-cipher"})
    )

    result = client.put_password(
        folder="vm-ephemeral/vm-1", name="heartbeat_access_token", value="hbt_new"
    )

    assert result == "existing-cipher"


@respx.mock
def test_put_password_creates_folder_if_missing(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(return_value=_empty_sync())
    respx.post("https://vw.test/api/folders").mock(
        return_value=httpx.Response(200, json={"Id": "f-new", "Name": "vm-ephemeral/vm-99"})
    )
    respx.post("https://vw.test/api/ciphers").mock(
        return_value=httpx.Response(200, json={"Id": "c-1"})
    )

    result = client.put_password(folder="vm-ephemeral/vm-99", name="token", value="v")

    assert result == "c-1"


@respx.mock
def test_put_password_auth_failure(client):
    respx.post("https://vw.test/identity/connect/token").mock(
        return_value=httpx.Response(401, json={})
    )
    with pytest.raises(VaultwardenAuthError):
        client.put_password(folder="x", name="y", value="z")


# ------------------------------------------------- transport-error translation
#
# Write paths promise "transient outage surfaces as VaultwardenError"
# (see vw_vm_secret_store docstring) — a raw httpx.ConnectError leaking
# through breaks that contract.


@respx.mock
def test_put_password_sync_transport_error_raises_vaultwarden_error(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(VaultwardenError):
        client.put_password(folder="x", name="y", value="z")


@respx.mock
def test_put_password_folder_create_transport_error_raises_vaultwarden_error(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(return_value=_empty_sync())
    respx.post("https://vw.test/api/folders").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(VaultwardenError):
        client.put_password(folder="vm-ephemeral/vm-9", name="tok", value="v")


@respx.mock
def test_put_password_cipher_write_transport_error_raises_vaultwarden_error(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(
        return_value=_empty_sync(folder_name="vm-ephemeral/vm-1", folder_id="f-1")
    )
    respx.post("https://vw.test/api/ciphers").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(VaultwardenError):
        client.put_password(folder="vm-ephemeral/vm-1", name="tok", value="v")


@respx.mock
def test_delete_password_transport_error_raises_vaultwarden_error(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "Folders": [{"Id": "f-1", "Name": "vm-ephemeral/vm-1"}],
                "Ciphers": [
                    {"Id": "c-1", "Name": "tok", "FolderId": "f-1", "Login": {"Password": "v"}}
                ],
            },
        )
    )
    respx.delete("https://vw.test/api/ciphers/c-1").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(VaultwardenError):
        client.delete_password(folder="vm-ephemeral/vm-1", name="tok")


# -------------------------------------------------------------- delete_password


@respx.mock
def test_delete_password_removes_cipher(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "Folders": [{"Id": "f-1", "Name": "vm-ephemeral/vm-1"}],
                "Ciphers": [
                    {
                        "Id": "c-1",
                        "Name": "tok",
                        "FolderId": "f-1",
                        "Login": {"Password": "v"},
                    }
                ],
            },
        )
    )
    deleted = respx.delete("https://vw.test/api/ciphers/c-1").mock(
        return_value=httpx.Response(200, json={})
    )

    client.delete_password(folder="vm-ephemeral/vm-1", name="tok")

    assert deleted.called


@respx.mock
def test_delete_password_is_idempotent_when_absent(client):
    """Deleting a non-existent secret is a no-op (idempotent semantics)."""
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(return_value=_empty_sync())

    # Should NOT raise — caller wanted it gone, it's gone.
    client.delete_password(folder="nope", name="nope")


# ----------------------------------------------------------------- list_folder


@respx.mock
def test_list_folder_returns_names(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "Folders": [{"Id": "f-1", "Name": "vm-ephemeral/vm-1"}],
                "Ciphers": [
                    {"Id": "c-1", "Name": "tok-a", "FolderId": "f-1", "Login": {"Password": "x"}},
                    {"Id": "c-2", "Name": "tok-b", "FolderId": "f-1", "Login": {"Password": "y"}},
                    {
                        "Id": "c-3",
                        "Name": "other",
                        "FolderId": "f-2",
                        "Login": {"Password": "z"},
                    },
                ],
            },
        )
    )

    names = client.list_folder("vm-ephemeral/vm-1")

    assert sorted(names) == ["tok-a", "tok-b"]


@respx.mock
def test_list_folder_returns_empty_when_folder_absent(client):
    respx.post("https://vw.test/identity/connect/token").mock(return_value=_identity_ok())
    respx.get("https://vw.test/api/sync").mock(return_value=_empty_sync())

    assert client.list_folder("nope") == []
