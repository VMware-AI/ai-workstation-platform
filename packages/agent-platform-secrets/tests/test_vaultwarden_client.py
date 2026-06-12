"""VaultwardenClient tests — HTTP layer mocked with respx."""

from __future__ import annotations

import httpx
import pytest
import respx
from agent_platform_secrets import (
    VaultwardenAuthError,
    VaultwardenClient,
    VaultwardenError,
    VaultwardenNotFoundError,
)


def _identity_ok() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "fake-jwt", "expires_in": 3600, "token_type": "Bearer"}
    )


def _sync_with_one_cipher(
    folder_name: str | None, cipher_name: str, password: str
) -> httpx.Response:
    folders = []
    folder_id = None
    if folder_name is not None:
        folder_id = "folder-uuid-1"
        folders = [{"Id": folder_id, "Name": folder_name}]
    return httpx.Response(
        200,
        json={
            "Folders": folders,
            "Ciphers": [
                {
                    "Id": "cipher-uuid-1",
                    "Name": cipher_name,
                    "FolderId": folder_id,
                    "Login": {"Username": "svc", "Password": password},
                }
            ],
        },
    )


def test_fetch_password_happy_path() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(
            return_value=_sync_with_one_cipher("nsx", "api-key", "supersecret")
        )
        with VaultwardenClient(
            base_url="https://vault.example",
            client_id="cid",
            client_secret="csec",
        ) as c:
            assert c.fetch_password(folder="nsx", name="api-key") == "supersecret"


def test_identity_401_raises_auth_error() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(
            return_value=httpx.Response(401, json={"error": "invalid_client"})
        )
        with (
            VaultwardenClient(
                base_url="https://vault.example", client_id="x", client_secret="y"
            ) as c,
            pytest.raises(VaultwardenAuthError),
        ):
            c.fetch_password(folder="nsx", name="api-key")


def test_identity_500_raises_generic_error() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=httpx.Response(500))
        with (
            VaultwardenClient(
                base_url="https://vault.example", client_id="x", client_secret="y"
            ) as c,
            pytest.raises(VaultwardenError),
        ):
            c.fetch_password(folder="nsx", name="api-key")


def test_identity_missing_token_field_raises() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(
            return_value=httpx.Response(200, json={"expires_in": 3600})
        )
        with (
            VaultwardenClient(
                base_url="https://vault.example", client_id="x", client_secret="y"
            ) as c,
            pytest.raises(VaultwardenError, match="access_token"),
        ):
            c.fetch_password(folder="nsx", name="api-key")


def test_missing_folder_raises_not_found() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(
            return_value=httpx.Response(200, json={"Folders": [], "Ciphers": []})
        )
        with (
            VaultwardenClient(
                base_url="https://vault.example", client_id="x", client_secret="y"
            ) as c,
            pytest.raises(VaultwardenNotFoundError, match="folder 'nsx'"),
        ):
            c.fetch_password(folder="nsx", name="api-key")


def test_missing_cipher_raises_not_found() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(return_value=_sync_with_one_cipher("nsx", "other-cipher", "p"))
        with (
            VaultwardenClient(
                base_url="https://vault.example", client_id="x", client_secret="y"
            ) as c,
            pytest.raises(VaultwardenNotFoundError, match="api-key"),
        ):
            c.fetch_password(folder="nsx", name="api-key")


def test_top_level_cipher_resolves_with_empty_folder() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(return_value=_sync_with_one_cipher(None, "admin-token", "abc"))
        with VaultwardenClient(
            base_url="https://vault.example", client_id="x", client_secret="y"
        ) as c:
            assert c.fetch_password(folder="", name="admin-token") == "abc"


def test_folder_match_is_case_insensitive() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(return_value=_sync_with_one_cipher("NSX", "api-key", "secret"))
        with VaultwardenClient(
            base_url="https://vault.example", client_id="x", client_secret="y"
        ) as c:
            assert c.fetch_password(folder="nsx", name="api-key") == "secret"


def test_repr_never_leaks_secret_material() -> None:
    """%r logging / assertion diffs must not expose client_secret or the JWT."""
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(return_value=_sync_with_one_cipher("nsx", "api-key", "s"))
        with VaultwardenClient(
            base_url="https://vault.example",
            client_id="cid",
            client_secret="csec-SENSITIVE",
        ) as c:
            c.fetch_password(folder="nsx", name="api-key")  # populates _token
            assert "csec-SENSITIVE" not in repr(c)
            assert "fake-jwt" not in repr(c)
            assert c._token is not None
            assert "fake-jwt" not in repr(c._token)


def test_fetch_password_retry_transport_error_raises_vaultwarden_error() -> None:
    """The 401-retry second GET must also translate httpx transport errors."""
    with respx.mock(base_url="https://vault.example") as router:
        router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(
            side_effect=[httpx.Response(401), httpx.ConnectError("connection refused")]
        )
        with (
            VaultwardenClient(
                base_url="https://vault.example", client_id="x", client_secret="y"
            ) as c,
            pytest.raises(VaultwardenError),
        ):
            c.fetch_password(folder="nsx", name="api-key")


def test_token_cached_across_calls() -> None:
    with respx.mock(base_url="https://vault.example") as router:
        identity = router.post("/identity/connect/token").mock(return_value=_identity_ok())
        router.get("/api/sync").mock(return_value=_sync_with_one_cipher("nsx", "api-key", "s"))
        with VaultwardenClient(
            base_url="https://vault.example", client_id="x", client_secret="y"
        ) as c:
            c.fetch_password(folder="nsx", name="api-key")
            c.fetch_password(folder="nsx", name="api-key")
        assert identity.call_count == 1
