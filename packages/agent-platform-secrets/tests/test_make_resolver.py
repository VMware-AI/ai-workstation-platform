"""make_resolver factory tests (harness H-11, #212).

One shared entry point for control + gateway bootstrap, so the two
services can't drift on how a resolver is constructed (family-fix rule:
shared patterns get one implementation).
"""

from __future__ import annotations

import pytest
from agent_platform_secrets import (
    EnvSecretResolver,
    VaultwardenSecretResolver,
    make_resolver,
)


def test_no_url_returns_env_resolver() -> None:
    resolver = make_resolver(base_url="", client_id="", client_secret="")
    assert isinstance(resolver, EnvSecretResolver)


def test_none_url_returns_env_resolver() -> None:
    resolver = make_resolver(base_url=None, client_id="", client_secret="")
    assert isinstance(resolver, EnvSecretResolver)


def test_url_returns_vaultwarden_resolver() -> None:
    resolver = make_resolver(
        base_url="https://vw.internal:8080",
        client_id="user.abc",
        client_secret="s3cret",
    )
    assert isinstance(resolver, VaultwardenSecretResolver)
    assert resolver.client.base_url == "https://vw.internal:8080"
    assert resolver.client.client_id == "user.abc"
    assert resolver.client.client_secret == "s3cret"


def test_url_without_credentials_is_a_config_error() -> None:
    # A vault URL with no API key is a misconfiguration — fail loudly at
    # construction, not with a confusing 401 at first resolve.
    with pytest.raises(ValueError, match="client_id"):
        make_resolver(base_url="https://vw.internal:8080", client_id="", client_secret="x")
    with pytest.raises(ValueError, match="client_secret"):
        make_resolver(base_url="https://vw.internal:8080", client_id="x", client_secret="")


def test_timeout_passthrough() -> None:
    resolver = make_resolver(
        base_url="https://vw.internal:8080",
        client_id="user.abc",
        client_secret="s3cret",
        timeout_s=2.5,
    )
    assert isinstance(resolver, VaultwardenSecretResolver)
    assert resolver.client.timeout_s == 2.5


def test_plaintext_http_base_url_rejected() -> None:
    # SEC-18: a non-localhost http:// base_url would send the client_secret
    # and fetched secrets in cleartext — refuse it.
    with pytest.raises(ValueError, match="must be https"):
        make_resolver(
            base_url="http://vw.internal:8080", client_id="user.abc", client_secret="s3cret"
        )


def test_localhost_http_base_url_allowed_for_dev() -> None:
    resolver = make_resolver(
        base_url="http://localhost:8080", client_id="user.abc", client_secret="s3cret"
    )
    assert isinstance(resolver, VaultwardenSecretResolver)
