"""SecretReference parsing tests."""

from __future__ import annotations

import pytest
from agent_platform_secrets import (
    SecretReferenceError,
    parse_reference,
)
from agent_platform_secrets.reference import (
    EnvReference,
    LiteralReference,
    VaultReference,
)


def test_plain_string_parses_as_literal() -> None:
    ref = parse_reference("hunter2")
    assert isinstance(ref, LiteralReference)
    assert ref.value == "hunter2"


def test_empty_string_parses_as_literal() -> None:
    # An empty value is a valid (if surprising) literal — operators
    # who really want "no secret" use it. Loud-fail on a misspelt
    # scheme is the more common case we care about.
    assert parse_reference("") == LiteralReference("")


def test_env_reference_extracts_var_name() -> None:
    ref = parse_reference("env://NSX_PASSWORD")
    assert ref == EnvReference(var="NSX_PASSWORD")


def test_env_reference_rejects_empty_var() -> None:
    with pytest.raises(SecretReferenceError, match="variable name"):
        parse_reference("env://")


def test_vault_reference_with_path() -> None:
    ref = parse_reference("vault://nsx/api-key")
    assert ref == VaultReference(path="nsx", name="api-key")


def test_vault_reference_with_nested_path_keeps_full_path() -> None:
    ref = parse_reference("vault://prod/cluster-a/admin")
    assert isinstance(ref, VaultReference)
    assert ref.path == "prod/cluster-a"
    assert ref.name == "admin"
    assert ref.full == "prod/cluster-a/admin"


def test_vault_reference_without_path_keeps_path_empty() -> None:
    ref = parse_reference("vault://api-key")
    assert ref == VaultReference(path="", name="api-key")
    assert ref.full == "api-key"


def test_vault_reference_rejects_empty_body() -> None:
    with pytest.raises(SecretReferenceError, match="path/name"):
        parse_reference("vault://")


def test_unknown_scheme_raises() -> None:
    with pytest.raises(SecretReferenceError, match="vlt"):
        parse_reference("vlt://nsx/api-key")


def test_scheme_match_is_case_insensitive() -> None:
    assert isinstance(parse_reference("ENV://FOO"), EnvReference)
    assert isinstance(parse_reference("Vault://x"), VaultReference)
