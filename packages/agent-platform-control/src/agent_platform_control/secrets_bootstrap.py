"""Resolve secret references in Settings at startup (C18 wiring, H-11 #212).

Secret-bearing fields accept three forms (see agent_platform_secrets):

* plain literal — current dev/.env posture, passes through untouched
* ``env://VAR`` — indirection through the process environment
* ``vault://<folder>/<item>`` — fetched from Vaultwarden (requires
  ``AGENT_PLATFORM_VAULTWARDEN_URL`` + client credentials)

``get_settings()`` calls :func:`resolve_secret_settings` once, so every
consumer downstream sees plaintext and an unreachable/misconfigured vault
refuses startup with a teaching error instead of a first-request 500.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_platform_secrets import (
    SecretReferenceError,
    SecretResolver,
    SecretResolverError,
    VaultwardenError,
    make_resolver,
    parse_reference,
)

if TYPE_CHECKING:  # pragma: no cover — import-cycle guard (config imports us)
    from .config import Settings

# Every field here holds a secret and may carry a reference. Extend this
# tuple when adding a new secret-bearing setting — nothing else to wire.
SECRET_FIELDS: tuple[str, ...] = (
    "admin_api_token",
    "c1_ingest_service_token",
    "deployment_token_fernet_key",
    "vsphere_password",
)

# URL-shaped secret fields (#225): the value is itself a URL, so a bare
# "://" test would misread a postgresql:// literal as a reference. Same
# asymmetric rule as the gateway's database_url — only an explicit env://
# or vault:// scheme counts; anything else passes through untouched.
URL_SECRET_FIELDS: tuple[str, ...] = ("database_url",)
_REFERENCE_SCHEMES = ("env://", "vault://")


def _resolve_field(resolver: SecretResolver, field: str, raw: str) -> str:
    try:
        plaintext = resolver.resolve(parse_reference(raw))
    except (SecretReferenceError, SecretResolverError, VaultwardenError) as exc:
        raise RuntimeError(
            f"failed to resolve secret for setting {field!r}: {exc}. "
            "Fix the reference (literal, env://VAR, or vault://folder/item), "
            "check AGENT_PLATFORM_VAULTWARDEN_URL / _CLIENT_ID / _CLIENT_SECRET, "
            "and that Vaultwarden is reachable. See docs/architecture/34 §2.5."
        ) from exc
    # An env var set to "" resolves without error but would silently defeat
    # downstream non-empty checks (PR #220 review HIGH-1) — an empty secret
    # is always a misconfiguration.
    if not plaintext:
        raise RuntimeError(
            f"secret reference for setting {field!r} resolved to an empty "
            "value — the referenced env var / vault item is set but blank. "
            "Set a real secret value."
        )
    return plaintext


def resolve_secret_settings(settings: Settings) -> Settings:
    """Return Settings with all secret references resolved to plaintext.

    Pure literals (the default dev posture) short-circuit to the same
    instance — zero behaviour change and no resolver construction unless a
    reference is actually present.
    """
    referenced = {
        field: raw for field in SECRET_FIELDS if "://" in (raw := getattr(settings, field))
    }
    referenced |= {
        field: raw
        for field in URL_SECRET_FIELDS
        if (raw := getattr(settings, field)).lower().startswith(_REFERENCE_SCHEMES)
    }
    if not referenced:
        return settings

    if any(raw.lower().startswith("vault://") for raw in referenced.values()) and not (
        settings.vaultwarden_url
    ):
        raise RuntimeError(
            "config uses vault:// secret references but "
            "AGENT_PLATFORM_VAULTWARDEN_URL is not set. Set the Vaultwarden "
            "URL + AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID/_CLIENT_SECRET, or "
            "switch the references to env:// / literals."
        )

    try:
        resolver = make_resolver(
            base_url=settings.vaultwarden_url,
            client_id=settings.vaultwarden_client_id,
            client_secret=settings.vaultwarden_client_secret,
            timeout_s=settings.vaultwarden_timeout_s,
        )
    except ValueError as exc:
        raise RuntimeError(
            f"Vaultwarden secrets backend misconfigured: {exc}. Set "
            "AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID and "
            "AGENT_PLATFORM_VAULTWARDEN_CLIENT_SECRET (Vaultwarden API key)."
        ) from exc

    resolved = {field: _resolve_field(resolver, field, raw) for field, raw in referenced.items()}
    # Immutable update — callers holding the original never see it mutate.
    return settings.model_copy(update=resolved)
