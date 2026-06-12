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
* per-user ``rpm`` / ``tpm`` limits are configured under
  ``litellm_settings`` so Task 1.5.6 acceptance ("超额 log warning")
  cannot be defeated by simply not setting limits
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
    if not isinstance(settings, Mapping):
        raise GatewayConfigError("litellm_settings section is required")
    if not isinstance(settings.get("rpm"), int) or settings["rpm"] <= 0:
        raise GatewayConfigError(
            "litellm_settings.rpm must be a positive integer (Task 1.5.6 — "
            "rate-limit logging requires rpm to be set)"
        )
    if not isinstance(settings.get("tpm"), int) or settings["tpm"] <= 0:
        raise GatewayConfigError(
            "litellm_settings.tpm must be a positive integer (Task 1.5.6 — "
            "rate-limit logging requires tpm to be set)"
        )

    logger.info(
        "gateway config OK: %d model(s), rpm=%s, tpm=%s",
        len(model_list),
        settings["rpm"],
        settings["tpm"],
    )
