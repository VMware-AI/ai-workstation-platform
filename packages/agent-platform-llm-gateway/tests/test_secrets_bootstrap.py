"""C18 SecretResolver wiring into gateway startup (harness H-11, #212).

The gateway config may carry ``env://`` / ``vault://`` references in its
secret-bearing fields (master_key, database_url, per-model api_key) and in
the ``C1_INGEST_TOKEN`` env var. ``start`` resolves them before exec'ing
LiteLLM. Plaintext never lands on disk: resolved values go into process
env vars and the rewritten config uses LiteLLM's native ``os.environ/VAR``
indirection.
"""

from __future__ import annotations

import os

import pytest
import yaml
from agent_platform_llm_gateway.cli import app
from agent_platform_llm_gateway.secrets_bootstrap import (
    GatewaySecretsError,
    resolve_config_secrets,
    resolve_ingest_token_env,
)
from typer.testing import CliRunner


def _config(master_key: str = "sk-very-real-secret-token-xxxxxxxxxxxxxxxxxxxx") -> dict:
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
            "master_key": master_key,
            "database_url": "postgresql://agent-platform@localhost/agent_platform_gateway",
        },
        "litellm_settings": {"rpm": 100, "tpm": 50_000},
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "AGENT_PLATFORM_VAULTWARDEN_URL",
        "AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID",
        "AGENT_PLATFORM_VAULTWARDEN_CLIENT_SECRET",
        "C1_INGEST_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    # Production code writes AGENT_PLATFORM_RESOLVED_* into os.environ;
    # scrub them so tests can't see each other's resolutions (review L-5).
    for var in [k for k in os.environ if k.startswith("AGENT_PLATFORM_RESOLVED")]:
        del os.environ[var]


# ----- resolve_config_secrets ------------------------------------------------


def test_literal_config_passes_through_unchanged():
    cfg = _config()
    resolved, changed = resolve_config_secrets(cfg)
    assert changed is False
    assert resolved is cfg


def test_env_reference_in_master_key_resolves_via_environ_indirection(monkeypatch):
    monkeypatch.setenv("REAL_MASTER_KEY", "sk-prod-master")
    cfg = _config(master_key="env://REAL_MASTER_KEY")
    resolved, changed = resolve_config_secrets(cfg)
    assert changed is True
    indirect = resolved["general_settings"]["master_key"]
    # Rewritten to LiteLLM-native env indirection, never inline plaintext.
    assert indirect.startswith("os.environ/")
    var = indirect.removeprefix("os.environ/")
    assert os.environ[var] == "sk-prod-master"
    # Original config is untouched (immutable update).
    assert cfg["general_settings"]["master_key"] == "env://REAL_MASTER_KEY"


def test_env_reference_in_model_api_key_resolves(monkeypatch):
    monkeypatch.setenv("QWEN_KEY", "sk-qwen")
    cfg = _config()
    cfg["model_list"][0]["litellm_params"]["api_key"] = "env://QWEN_KEY"
    resolved, changed = resolve_config_secrets(cfg)
    assert changed is True
    indirect = resolved["model_list"][0]["litellm_params"]["api_key"]
    assert indirect.startswith("os.environ/")
    assert os.environ[indirect.removeprefix("os.environ/")] == "sk-qwen"


def test_vault_reference_without_vaultwarden_url_fails_fast():
    cfg = _config(master_key="vault://infra/litellm-master-key")
    with pytest.raises(GatewaySecretsError, match="AGENT_PLATFORM_VAULTWARDEN_URL"):
        resolve_config_secrets(cfg)


def test_vault_reference_with_unreachable_vaultwarden_fails_fast(monkeypatch):
    """Integration (issue #212 criteria): vault down → fail before exec."""
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID", "user.abc")
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_TIMEOUT_S", "0.2")
    cfg = _config(master_key="vault://infra/litellm-master-key")
    with pytest.raises(GatewaySecretsError, match="master_key"):
        resolve_config_secrets(cfg)


def test_unknown_scheme_fails_fast():
    cfg = _config(master_key="vlt://typo")
    with pytest.raises(GatewaySecretsError, match="master_key"):
        resolve_config_secrets(cfg)


def test_empty_resolved_value_is_rejected(monkeypatch):
    """review HIGH-1: env var set-but-blank must not hand LiteLLM an empty
    master_key (validator bypass -> potentially unauthenticated proxy)."""
    monkeypatch.setenv("BLANK_KEY", "")
    cfg = _config(master_key="env://BLANK_KEY")
    with pytest.raises(GatewaySecretsError, match="empty value"):
        resolve_config_secrets(cfg)


def test_literal_secrets_also_moved_off_disk_when_any_reference(monkeypatch):
    """review M-3: once the config is rewritten, literal master_key /
    database_url / api_key must move to env indirection too — the temp
    file must carry zero secret material."""
    monkeypatch.setenv("QWEN_KEY", "sk-qwen")
    cfg = _config()  # literal master_key + literal database_url
    cfg["model_list"][0]["litellm_params"]["api_key"] = "env://QWEN_KEY"
    resolved, changed = resolve_config_secrets(cfg)
    assert changed is True
    for value in (
        resolved["general_settings"]["master_key"],
        resolved["general_settings"]["database_url"],
        resolved["model_list"][0]["litellm_params"]["api_key"],
    ):
        assert value.startswith("os.environ/")
    assert os.environ[
        resolved["general_settings"]["database_url"].removeprefix("os.environ/")
    ].startswith("postgresql://")


def test_database_url_env_reference_resolves(monkeypatch):
    """review L-6: the accept branch of the asymmetric rule."""
    monkeypatch.setenv("DB_URL", "postgresql://u:pw@db/prod")
    cfg = _config()
    cfg["general_settings"]["database_url"] = "env://DB_URL"
    resolved, changed = resolve_config_secrets(cfg)
    assert changed is True
    var = resolved["general_settings"]["database_url"].removeprefix("os.environ/")
    assert os.environ[var] == "postgresql://u:pw@db/prod"


def test_multiple_model_api_keys_get_distinct_env_vars(monkeypatch):
    monkeypatch.setenv("K0", "sk-zero")
    monkeypatch.setenv("K1", "sk-one")
    cfg = _config()
    cfg["model_list"].append(
        {
            "model_name": "second",
            "litellm_params": {
                "model": "openai/second",
                "api_base": "http://localhost:8002/v1",
                "api_key": "env://K1",
            },
        }
    )
    cfg["model_list"][0]["litellm_params"]["api_key"] = "env://K0"
    resolved, _ = resolve_config_secrets(cfg)
    v0 = resolved["model_list"][0]["litellm_params"]["api_key"]
    v1 = resolved["model_list"][1]["litellm_params"]["api_key"]
    assert v0 != v1
    assert os.environ[v0.removeprefix("os.environ/")] == "sk-zero"
    assert os.environ[v1.removeprefix("os.environ/")] == "sk-one"


# ----- resolve_ingest_token_env ----------------------------------------------


def test_ingest_token_literal_left_alone(monkeypatch):
    monkeypatch.setenv("C1_INGEST_TOKEN", "plain-token")
    resolve_ingest_token_env()
    assert os.environ["C1_INGEST_TOKEN"] == "plain-token"


def test_ingest_token_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("REAL_INGEST", "resolved-token")
    monkeypatch.setenv("C1_INGEST_TOKEN", "env://REAL_INGEST")
    resolve_ingest_token_env()
    assert os.environ["C1_INGEST_TOKEN"] == "resolved-token"


def test_ingest_token_absent_is_fine():
    resolve_ingest_token_env()  # must not raise
    assert "C1_INGEST_TOKEN" not in os.environ


# ----- cli start integration -------------------------------------------------


def _write_config(tmp_path, cfg: dict) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def test_start_with_references_execs_rewritten_config(tmp_path, monkeypatch):
    monkeypatch.setenv("REAL_MASTER_KEY", "sk-prod-master")
    captured: dict[str, list[str]] = {}

    def fake_execvp(file, args):
        captured["args"] = list(args)

    monkeypatch.setattr(os, "execvp", fake_execvp)
    cfg_path = _write_config(tmp_path, _config(master_key="env://REAL_MASTER_KEY"))
    result = CliRunner().invoke(app, ["start", "--config", cfg_path])
    assert result.exit_code == 0

    resolved_path = captured["args"][captured["args"].index("--config") + 1]
    assert resolved_path != cfg_path
    # Resolved config: 0600, env indirection, no plaintext secret on disk.
    assert oct(os.stat(resolved_path).st_mode & 0o777) == "0o600"
    with open(resolved_path) as fh:
        content = fh.read()
    assert "sk-prod-master" not in content
    assert "postgresql://" not in content  # literal database_url moved off disk too (M-3)
    assert "os.environ/" in content
    os.unlink(resolved_path)


def test_start_without_references_execs_original_config(tmp_path, monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_execvp(file, args):
        captured["args"] = list(args)

    monkeypatch.setattr(os, "execvp", fake_execvp)
    cfg_path = _write_config(tmp_path, _config())
    result = CliRunner().invoke(app, ["start", "--config", cfg_path])
    assert result.exit_code == 0
    assert captured["args"][captured["args"].index("--config") + 1] == cfg_path


def test_start_with_unreachable_vault_exits_with_teaching_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_CLIENT_ID", "user.abc")
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("AGENT_PLATFORM_VAULTWARDEN_TIMEOUT_S", "0.2")
    monkeypatch.setattr(os, "execvp", lambda *a: pytest.fail("must not exec"))
    cfg_path = _write_config(tmp_path, _config(master_key="vault://infra/master"))
    result = CliRunner().invoke(app, ["start", "--config", cfg_path])
    assert result.exit_code == 1
    assert "secrets bootstrap failed" in result.output
