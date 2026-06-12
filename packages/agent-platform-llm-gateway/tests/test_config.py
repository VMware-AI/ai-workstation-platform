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
        "litellm_settings": {"rpm": 100, "tpm": 50_000},
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


def test_rejects_missing_rpm() -> None:
    cfg = _good_config()
    del cfg["litellm_settings"]["rpm"]
    with pytest.raises(GatewayConfigError, match="rpm"):
        validate_config(cfg)


def test_rejects_missing_tpm() -> None:
    cfg = _good_config()
    del cfg["litellm_settings"]["tpm"]
    with pytest.raises(GatewayConfigError, match="tpm"):
        validate_config(cfg)


def test_rejects_non_positive_rpm() -> None:
    cfg = _good_config()
    cfg["litellm_settings"]["rpm"] = 0
    with pytest.raises(GatewayConfigError, match="rpm"):
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
