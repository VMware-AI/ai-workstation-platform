"""Operator-config validator for the LiteLLM proxy.

LiteLLM accepts almost any YAML and only complains at request time
when a missing field bites. For a control-plane deployment we want
fail-fast: the systemd ExecStartPre runs this validator and refuses
to launch if any platform invariant is violated.

Platform invariants (over and above what LiteLLM enforces):

* ``model_list`` is non-empty
* ``general_settings.master_key`` is set and not the placeholder
* ``general_settings.database_url`` is set (spend tracking goes to
  the shared Postgres alongside C1)
* the config does NOT carry keys LiteLLM silently ignores (issue #351):
  ``litellm_settings.rpm`` / ``.tpm`` and ``anthropic_passthrough_endpoint``.
  These were assumed to enforce per-user limits / Anthropic routing but are
  not consumed by LiteLLM at all — leaving them in gives false security. Real
  per-user limits are ``rpm_limit`` / ``tpm_limit`` on the virtual key (set at
  generation via C1's ``/key/generate``); the Anthropic ``/v1/messages`` route
  is built into LiteLLM natively (no config key). We reject the dead keys so a
  failed assumption surfaces at validate-time instead of silently at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PLACEHOLDER_MASTER_KEY = "sk-CHANGE-ME-master-key"


class GatewayConfigError(ValueError):
    """Raised when the config file violates a platform invariant.

    Distinct from ``yaml.YAMLError`` so callers can tell parse errors
    apart from validation errors and surface clearer messages.
    """


def load_config(path: str | Path) -> Mapping[str, Any]:
    """Read YAML, return the parsed mapping. Raises on parse error."""
    text = Path(path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, Mapping):
        raise GatewayConfigError(f"config root must be a mapping, got {type(parsed).__name__}")
    return parsed


def validate_config(config: Mapping[str, Any]) -> None:
    """Assert every platform invariant on a loaded config. Raises on first failure."""
    model_list = config.get("model_list")
    if not isinstance(model_list, list) or len(model_list) == 0:
        raise GatewayConfigError("model_list must be a non-empty list")
    for idx, entry in enumerate(model_list):
        if not isinstance(entry, Mapping):
            raise GatewayConfigError(f"model_list[{idx}] must be a mapping")
        if "model_name" not in entry:
            raise GatewayConfigError(f"model_list[{idx}] missing 'model_name'")
        params = entry.get("litellm_params")
        if not isinstance(params, Mapping) or "model" not in params or "api_base" not in params:
            raise GatewayConfigError(
                f"model_list[{idx}].litellm_params must include 'model' and 'api_base'"
            )

    general = config.get("general_settings")
    if not isinstance(general, Mapping):
        raise GatewayConfigError("general_settings section is required")
    master_key = general.get("master_key")
    if not isinstance(master_key, str) or master_key in ("", _PLACEHOLDER_MASTER_KEY):
        raise GatewayConfigError(
            "general_settings.master_key must be set to a real secret "
            "(generate with: openssl rand -hex 32)"
        )
    if not general.get("database_url"):
        raise GatewayConfigError(
            "general_settings.database_url is required — spend tracking goes to "
            "the shared Postgres alongside C1"
        )

    settings = config.get("litellm_settings")
    if settings is not None and not isinstance(settings, Mapping):
        raise GatewayConfigError("litellm_settings must be a mapping when present")

    _reject_silently_ignored_keys(config, settings)

    logger.info("gateway config OK: %d model(s)", len(model_list))


# Anthropic routing is native in LiteLLM — no config key gates it (issue #351).
_ANTHROPIC_PASSTHROUGH_MSG = (
    "anthropic_passthrough_endpoint is not a LiteLLM config key — it is silently "
    "ignored. The Anthropic /v1/messages route (and the /anthropic/* pass-through) "
    "is built into the LiteLLM proxy natively via plain model_list mappings; remove "
    "this key. Point Claude Code at the proxy with ANTHROPIC_BASE_URL=<proxy>."
)

_RPM_TPM_MSG = (
    "rpm / tpm are NOT consumed by LiteLLM as global/proxy rate limits (silently "
    "ignored) — they do not rate-limit anything and give false security (issue "
    "#351). Per-user limits are rpm_limit / tpm_limit on the virtual key, set at "
    "generation time via C1's /key/generate (or team-level limits via /team/new). "
    "Remove rpm/tpm from {where}. (Model-level rpm/tpm under a model_list entry's "
    "litellm_params is a different, valid key and is not rejected.)"
)


def _reject_silently_ignored_keys(
    config: Mapping[str, Any], settings: Mapping[str, Any] | None
) -> None:
    """Fail fast on config keys LiteLLM accepts-and-ignores (issue #351), so a
    wrong assumption surfaces here instead of as silent runtime behaviour.

    rpm/tpm and anthropic_passthrough_endpoint are both checked at the top level
    *and* under litellm_settings — an operator migrating from another example may
    drop a dead key in either spot, and every form of the wrong assumption must
    surface loudly, not just the nested one.
    """
    if "rpm" in config or "tpm" in config:
        raise GatewayConfigError(_RPM_TPM_MSG.format(where="the config root"))
    if "anthropic_passthrough_endpoint" in config:
        raise GatewayConfigError(_ANTHROPIC_PASSTHROUGH_MSG)
    if isinstance(settings, Mapping):
        if "rpm" in settings or "tpm" in settings:
            raise GatewayConfigError(_RPM_TPM_MSG.format(where="litellm_settings"))
        if "anthropic_passthrough_endpoint" in settings:
            raise GatewayConfigError(_ANTHROPIC_PASSTHROUGH_MSG)
