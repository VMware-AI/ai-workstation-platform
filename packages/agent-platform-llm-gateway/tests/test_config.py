"""Config validator tests — gates Task 1.5.1 + 1.5.6 acceptance.

The validator is the operator's last line of defence against a typo
in production. Each invariant has its own test so a regression
points at the specific rule that loosened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from agent_platform_llm_gateway import GatewayConfigError, load_config, validate_config


def _good_config() -> dict[str, Any]:
    return {
        "model_list": [
            {
                "model_name": "qwen-coder-32b",
                "litellm_params": {
                    "model": "openai/qwen-coder-32b",
                    "api_base": "http://localhost:8001/v1",
                    "api_key": "dummy",
                },
            },
        ],
        "general_settings": {
            "master_key": "sk-very-real-secret-token-xxxxxxxxxxxxxxxxxxxx",
            "database_url": "postgresql://agent-platform@localhost/agent_platform_gateway",
        },
        # Per-user rate limits are NOT set here — LiteLLM ignores litellm_settings.rpm/tpm
        # (issue #351). Real limits are rpm_limit/tpm_limit on the virtual key at /key/generate.
        "litellm_settings": {"success_callback": ["postgres"]},
    }


def test_accepts_minimal_valid_config() -> None:
    validate_config(_good_config())  # must not raise


def test_rejects_empty_model_list() -> None:
    cfg = _good_config()
    cfg["model_list"] = []
    with pytest.raises(GatewayConfigError, match="non-empty"):
        validate_config(cfg)


def test_rejects_model_without_api_base() -> None:
    cfg = _good_config()
    del cfg["model_list"][0]["litellm_params"]["api_base"]
    with pytest.raises(GatewayConfigError, match="api_base"):
        validate_config(cfg)


def test_rejects_placeholder_master_key() -> None:
    cfg = _good_config()
    cfg["general_settings"]["master_key"] = "sk-CHANGE-ME-master-key"
    with pytest.raises(GatewayConfigError, match="master_key"):
        validate_config(cfg)


def test_rejects_missing_database_url() -> None:
    cfg = _good_config()
    del cfg["general_settings"]["database_url"]
    with pytest.raises(GatewayConfigError, match="database_url"):
        validate_config(cfg)


def test_accepts_config_without_rate_limit_keys() -> None:
    """rpm/tpm are no longer required under litellm_settings — LiteLLM ignores
    them there (issue #351), so demanding them gave false security."""
    cfg = _good_config()
    assert "rpm" not in cfg["litellm_settings"]
    validate_config(cfg)  # must not raise


def test_accepts_missing_litellm_settings() -> None:
    """litellm_settings is optional now that its only enforced contents (the
    silently-ignored rpm/tpm) are gone."""
    cfg = _good_config()
    del cfg["litellm_settings"]
    validate_config(cfg)  # must not raise


def test_rejects_litellm_settings_rpm_as_silently_ignored() -> None:
    """LiteLLM does not consume litellm_settings.rpm — reject it loudly so it
    can't masquerade as a working rate limit (issue #351)."""
    cfg = _good_config()
    cfg["litellm_settings"]["rpm"] = 100
    with pytest.raises(GatewayConfigError, match="rpm_limit"):
        validate_config(cfg)


def test_rejects_litellm_settings_tpm_as_silently_ignored() -> None:
    cfg = _good_config()
    cfg["litellm_settings"]["tpm"] = 50_000
    with pytest.raises(GatewayConfigError, match="tpm_limit"):
        validate_config(cfg)


def test_rejects_top_level_rpm_as_silently_ignored() -> None:
    """rpm dropped at the config root (not under litellm_settings) is also
    silently ignored — an operator migrating from another example may put it
    there, so it must surface loudly too (issue #351 review)."""
    cfg = _good_config()
    cfg["rpm"] = 100
    with pytest.raises(GatewayConfigError, match="rpm_limit"):
        validate_config(cfg)


def test_rejects_top_level_tpm_as_silently_ignored() -> None:
    cfg = _good_config()
    cfg["tpm"] = 50_000
    with pytest.raises(GatewayConfigError, match="tpm_limit"):
        validate_config(cfg)


def test_accepts_model_level_rpm_under_litellm_params() -> None:
    """Model-level rpm under a model_list entry's litellm_params IS a valid
    LiteLLM load-balancing key — it must NOT be rejected (only global/proxy
    rpm/tpm are dead)."""
    cfg = _good_config()
    cfg["model_list"][0]["litellm_params"]["rpm"] = 100
    validate_config(cfg)  # must not raise


def test_rejects_anthropic_passthrough_endpoint_key() -> None:
    """anthropic_passthrough_endpoint is not a real LiteLLM key — the Anthropic
    /v1/messages route is native. Reject it so operators don't trust dead config."""
    cfg = _good_config()
    cfg["litellm_settings"]["anthropic_passthrough_endpoint"] = {"enabled": True}
    with pytest.raises(GatewayConfigError, match="anthropic_passthrough_endpoint"):
        validate_config(cfg)


def test_load_config_round_trips_through_yaml(tmp_path: Path) -> None:
    cfg = _good_config()
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    loaded = load_config(p)
    validate_config(loaded)


def test_load_config_rejects_non_mapping_root(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(GatewayConfigError, match="mapping"):
        load_config(p)
