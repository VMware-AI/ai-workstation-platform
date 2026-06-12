"""SecretResolver tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agent_platform_secrets import (
    EnvSecretResolver,
    SecretResolverError,
    VaultwardenSecretResolver,
    parse_reference,
)
from agent_platform_secrets.reference import LiteralReference, VaultReference
from agent_platform_secrets.vaultwarden_client import (
    VaultwardenAuthError,
    VaultwardenError,
    VaultwardenNotFoundError,
)

# ---- EnvSecretResolver ------------------------------------------------------


def test_env_resolver_returns_literal_unchanged() -> None:
    r = EnvSecretResolver()
    assert r.resolve(LiteralReference("hunter2")) == "hunter2"


def test_env_resolver_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "shh")
    r = EnvSecretResolver()
    assert r.resolve(parse_reference("env://MY_SECRET")) == "shh"


def test_env_resolver_raises_on_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_MISSING_VAR", raising=False)
    r = EnvSecretResolver()
    with pytest.raises(SecretResolverError, match="MY_MISSING_VAR"):
        r.resolve(parse_reference("env://MY_MISSING_VAR"))


def test_env_resolver_loud_fails_on_vault_ref() -> None:
    r = EnvSecretResolver()
    with pytest.raises(SecretResolverError, match="vault://"):
        r.resolve(parse_reference("vault://x/y"))


# ---- VaultwardenSecretResolver ---------------------------------------------


def test_vault_resolver_passes_literal_through() -> None:
    client = MagicMock()
    r = VaultwardenSecretResolver(client=client)
    assert r.resolve(LiteralReference("plain")) == "plain"
    client.fetch_password.assert_not_called()


def test_vault_resolver_reads_env_var_without_client_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOO", "bar")
    client = MagicMock()
    r = VaultwardenSecretResolver(client=client)
    assert r.resolve(parse_reference("env://FOO")) == "bar"
    client.fetch_password.assert_not_called()


def test_vault_resolver_calls_client_for_vault_ref() -> None:
    client = MagicMock()
    client.fetch_password.return_value = "topsecret"
    r = VaultwardenSecretResolver(client=client)
    assert r.resolve(parse_reference("vault://nsx/api-key")) == "topsecret"
    client.fetch_password.assert_called_once_with(folder="nsx", name="api-key")


def test_vault_resolver_translates_not_found_to_resolver_error() -> None:
    client = MagicMock()
    client.fetch_password.side_effect = VaultwardenNotFoundError("cipher x not found")
    r = VaultwardenSecretResolver(client=client)
    with pytest.raises(SecretResolverError, match="not found"):
        r.resolve(VaultReference(path="nsx", name="api-key"))


def test_vault_resolver_translates_auth_error_to_resolver_error() -> None:
    """The SecretResolver Protocol promises SecretResolverError — a raw
    VaultwardenAuthError leaking through bypasses callers' error handling."""
    client = MagicMock()
    client.fetch_password.side_effect = VaultwardenAuthError("identity returned 401")
    r = VaultwardenSecretResolver(client=client)
    with pytest.raises(SecretResolverError, match="auth"):
        r.resolve(VaultReference(path="nsx", name="api-key"))


def test_vault_resolver_translates_transport_error_to_resolver_error() -> None:
    client = MagicMock()
    client.fetch_password.side_effect = VaultwardenError("sync request failed: conn refused")
    r = VaultwardenSecretResolver(client=client)
    with pytest.raises(SecretResolverError, match="Vaultwarden"):
        r.resolve(VaultReference(path="nsx", name="api-key"))
