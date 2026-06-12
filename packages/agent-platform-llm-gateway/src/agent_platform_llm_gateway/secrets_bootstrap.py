"""Resolve secret references in the gateway config at startup (H-11, #212).

Secret-bearing fields — ``general_settings.master_key``,
``general_settings.database_url``, each model's ``litellm_params.api_key`` —
and the ``C1_INGEST_TOKEN`` env var may carry ``env://VAR`` or
``vault://<folder>/<item>`` references (see agent_platform_secrets).

``start`` resolves them *before* exec'ing LiteLLM, in keeping with the
validator philosophy: a misconfigured/unreachable vault fails in <1 second,
not after LiteLLM half-initialises. When the config is rewritten, NO secret
value — resolved or literal — lands on disk: every secret-bearing field is
moved into an ``AGENT_PLATFORM_RESOLVED_*`` env var (inherited across
execvp) and the rewritten config carries only LiteLLM-native
``os.environ/VAR`` indirections (PR #220 review M-3).
"""

from __future__ import annotations

import copy
import os

from agent_platform_secrets import (
    SecretReferenceError,
    SecretResolver,
    SecretResolverError,
    VaultwardenError,
    make_resolver,
    parse_reference,
)


class GatewaySecretsError(RuntimeError):
    """A secret reference could not be resolved at startup."""


_ENV_PREFIX = "AGENT_PLATFORM_RESOLVED"
_REFERENCE_SCHEMES = ("env", "vault")


def _resolver_from_env() -> SecretResolver:
    # Parse the timeout outside the make_resolver try so a malformed value
    # gets its own teaching error, not the credentials one (review L-4).
    raw_timeout = os.environ.get("AGENT_PLATFORM_VAULTWARDEN_TIMEOUT_S", "10")
    try:
        timeout_s = float(raw_timeout)
    except ValueError as exc:
        raise GatewaySecretsError(
            f"AGENT_PLATFORM_VAULTWARDEN_TIMEOUT_S must be a number, got {raw_timeout!r}."
        ) from exc
    try:
        return make_resolver(
            base_url=os.environ.get("AGENT_PLATFORM_VAULTWARDEN_URL", ""),
            client_id=os.environ.get("AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID", ""),
            client_secret=os.environ.get("AGENT_PLATFORM_VAULTWARDEN_CLIENT_SECRET", ""),
            timeout_s=timeout_s,
        )
    except ValueError as exc:
        raise GatewaySecretsError(
            f"Vaultwarden secrets backend misconfigured: {exc}. Set "
            "AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID and "
            "AGENT_PLATFORM_VAULTWARDEN_CLIENT_SECRET (Vaultwarden API key)."
        ) from exc


def _resolve(resolver: SecretResolver, field: str, raw: str) -> str:
    if raw.lower().startswith("vault://") and not os.environ.get("AGENT_PLATFORM_VAULTWARDEN_URL"):
        raise GatewaySecretsError(
            f"config field {field!r} uses a vault:// reference but "
            "AGENT_PLATFORM_VAULTWARDEN_URL is not set. Set the Vaultwarden "
            "URL + client credentials, or switch to env:// / a literal."
        )
    try:
        plaintext = resolver.resolve(parse_reference(raw))
    except (SecretReferenceError, SecretResolverError, VaultwardenError) as exc:
        raise GatewaySecretsError(
            f"failed to resolve secret for config field {field!r}: {exc}. "
            "Fix the reference (literal, env://VAR, or vault://folder/item) "
            "and check that Vaultwarden is reachable."
        ) from exc
    # An env var set to "" resolves without error but would hand LiteLLM an
    # empty master_key/api_key — defeating the validator's non-placeholder
    # check and potentially leaving the proxy unauthenticated (PR #220
    # review HIGH-1). An empty secret is always a misconfiguration.
    if not plaintext:
        raise GatewaySecretsError(
            f"secret reference for config field {field!r} resolved to an "
            "empty value — the referenced env var / vault item is set but "
            "blank. Set a real secret value."
        )
    return plaintext


def _is_reference(key: str, raw: str) -> bool:
    """True when ``raw`` should be parsed as a secret reference.

    database_url legitimately contains "://" (postgresql://…); only known
    reference schemes count there. master_key has no legitimate URL form,
    so any "://" there is a (possibly typo'd) reference — resolve it and
    let parse_reference fail loudly on unknown schemes.
    """
    if "://" not in raw or raw.startswith("os.environ/"):
        return False
    scheme = raw.split("://", 1)[0].lower()
    return scheme in _REFERENCE_SCHEMES or key == "master_key" or key == "api_key"


def resolve_config_secrets(cfg: dict) -> tuple[dict, bool]:
    """Return (config, changed).

    A reference-free config returns the same object untouched. When any
    reference is present, ALL secret-bearing fields (references AND
    literals) are moved into env vars and replaced with ``os.environ/<var>``
    indirections — the rewritten file that ``start`` writes to disk must
    carry no secret material at all, since literal master_key/database_url
    values would otherwise be duplicated into a /tmp file that outlives the
    process (PR #220 review M-3).
    """
    # (label for errors, env var name, raw value, is_reference)
    sites: list[tuple[str, str, str, bool]] = []

    general = cfg.get("general_settings") or {}
    for key in ("master_key", "database_url"):
        raw = general.get(key)
        if isinstance(raw, str) and raw and not raw.startswith("os.environ/"):
            sites.append(
                (
                    f"general_settings.{key}",
                    f"{_ENV_PREFIX}_{key.upper()}",
                    raw,
                    _is_reference(key, raw),
                )
            )

    for i, entry in enumerate(cfg.get("model_list") or []):
        params = entry.get("litellm_params") or {}
        raw = params.get("api_key")
        if isinstance(raw, str) and raw and not raw.startswith("os.environ/"):
            sites.append(
                (
                    f"model_list[{i}].litellm_params.api_key",
                    f"{_ENV_PREFIX}_API_KEY_{i}",
                    raw,
                    _is_reference("api_key", raw),
                )
            )

    if not any(is_ref for _, _, _, is_ref in sites):
        return cfg, False

    resolver = _resolver_from_env()
    resolved_cfg = copy.deepcopy(cfg)
    for label, env_var, raw, is_ref in sites:
        plaintext = _resolve(resolver, label, raw) if is_ref else raw
        os.environ[env_var] = plaintext
        indirect = f"os.environ/{env_var}"
        if label.startswith("general_settings."):
            resolved_cfg["general_settings"][label.removeprefix("general_settings.")] = indirect
        else:
            index = int(label.split("[", 1)[1].split("]", 1)[0])
            resolved_cfg["model_list"][index]["litellm_params"]["api_key"] = indirect
    return resolved_cfg, True


def resolve_ingest_token_env() -> None:
    """Resolve ``C1_INGEST_TOKEN`` in place when it carries a reference.

    The UsageCallback (running inside LiteLLM after execvp) reads this var;
    resolving here means the vault is only contacted once, at startup."""
    raw = os.environ.get("C1_INGEST_TOKEN", "")
    if "://" not in raw:
        return
    os.environ["C1_INGEST_TOKEN"] = _resolve(_resolver_from_env(), "C1_INGEST_TOKEN", raw)
