"""C18 SecretResolver wiring into control bootstrap (harness H-11, #212).

Secret-bearing settings fields accept ``env://`` / ``vault://`` references;
``get_settings()`` returns the resolved values. A vault reference with an
unreachable Vaultwarden must fail startup with a teaching error — not 500
on first use.
"""

from __future__ import annotations

import pytest
from agent_platform_control import config
from agent_platform_control.config import Settings, get_settings
from agent_platform_control.secrets_bootstrap import resolve_secret_settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ----- literal passthrough (current posture, must stay zero-change) --------


def test_all_literals_returns_settings_unchanged():
    settings = _settings()
    assert resolve_secret_settings(settings) is settings


# ----- env:// indirection ---------------------------------------------------


def test_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("MY_ADMIN_TOKEN", "real-admin-token")
    settings = _settings(admin_api_token="env://MY_ADMIN_TOKEN")
    resolved = resolve_secret_settings(settings)
    assert resolved.admin_api_token == "real-admin-token"
    # Immutable update: the original is untouched.
    assert settings.admin_api_token == "env://MY_ADMIN_TOKEN"


def test_missing_env_reference_fails_with_teaching_error(monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    settings = _settings(admin_api_token="env://NOPE_TOKEN")
    with pytest.raises(RuntimeError, match=r"admin_api_token.*NOPE_TOKEN"):
        resolve_secret_settings(settings)


def test_all_secret_fields_are_resolved(monkeypatch):
    monkeypatch.setenv("S1", "v1")
    monkeypatch.setenv("S2", "v2")
    monkeypatch.setenv("S3", "v3")
    monkeypatch.setenv("S4", "v4")
    settings = _settings(
        admin_api_token="env://S1",
        c1_ingest_service_token="env://S2",
        deployment_token_fernet_key="env://S3",
        vsphere_password="env://S4",
    )
    resolved = resolve_secret_settings(settings)
    assert resolved.admin_api_token == "v1"
    assert resolved.c1_ingest_service_token == "v2"
    assert resolved.deployment_token_fernet_key == "v3"
    assert resolved.vsphere_password == "v4"


def test_empty_env_reference_value_is_rejected(monkeypatch):
    """review HIGH-1: a set-but-blank env var must fail fast, not silently
    install an empty admin token."""
    monkeypatch.setenv("BLANK_TOKEN", "")
    settings = _settings(admin_api_token="env://BLANK_TOKEN")
    with pytest.raises(RuntimeError, match="empty value"):
        resolve_secret_settings(settings)


# ----- vault:// failure modes (issue #212 success criteria) -----------------


def test_vault_reference_without_vaultwarden_url_fails_fast():
    settings = _settings(admin_api_token="vault://infra/admin-token")
    with pytest.raises(RuntimeError, match="AGENT_PLATFORM_VAULTWARDEN_URL"):
        resolve_secret_settings(settings)


def test_vault_reference_with_unreachable_vaultwarden_fails_fast():
    """Integration: Vaultwarden down → startup refuses with a teaching error."""
    settings = _settings(
        admin_api_token="vault://infra/admin-token",
        vaultwarden_url="http://127.0.0.1:1",  # nothing listens here
        vaultwarden_client_id="user.abc",
        vaultwarden_client_secret="s3cret",
        vaultwarden_timeout_s=0.2,
    )
    with pytest.raises(RuntimeError) as excinfo:
        resolve_secret_settings(settings)
    message = str(excinfo.value)
    assert "admin_api_token" in message
    assert "Vaultwarden" in message


def test_unknown_scheme_fails_fast():
    settings = _settings(admin_api_token="vlt://typo/path")
    with pytest.raises(RuntimeError, match="admin_api_token"):
        resolve_secret_settings(settings)


# ----- get_settings() integration -------------------------------------------


def test_get_settings_returns_resolved_values(monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_ADMIN_API_TOKEN", "env://REAL_TOKEN")
    monkeypatch.setenv("REAL_TOKEN", "resolved-at-startup")
    config.get_settings.cache_clear()
    try:
        assert get_settings().admin_api_token == "resolved-at-startup"
    finally:
        config.get_settings.cache_clear()


# ----- database_url: URL-shaped field, asymmetric rule (#225) ---------------


def test_database_url_literal_passes_through_unchanged():
    """postgresql:// contains '://' but is NOT a reference — the asymmetric
    rule (only env:// / vault:// schemes count) must keep the zero-change
    short-circuit for literals."""
    settings = _settings(database_url="postgresql+asyncpg://user:pw@db:5432/c1")
    assert resolve_secret_settings(settings) is settings


def test_database_url_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("C1_DB_URL", "postgresql+asyncpg://svc:s3cret@db:5432/c1")
    settings = _settings(database_url="env://C1_DB_URL")
    resolved = resolve_secret_settings(settings)
    assert resolved.database_url == "postgresql+asyncpg://svc:s3cret@db:5432/c1"
    assert settings.database_url == "env://C1_DB_URL"  # immutable update


def test_database_url_vault_reference_without_url_fails_fast():
    settings = _settings(database_url="vault://infra/c1-db")
    with pytest.raises(RuntimeError, match="AGENT_PLATFORM_VAULTWARDEN_URL"):
        resolve_secret_settings(settings)
