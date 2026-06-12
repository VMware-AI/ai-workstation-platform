"""Cloud-init userdata template smoke tests (PR-A 2026-05-29).

Renders user-data.yaml.tpl with sample values and asserts that the agent
plugin contract (decision 2D), per-VM owner account (decision 1B), and
single-use bootstrap token (decision 4 / 8) all land correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

TPL_PATH = Path(__file__).resolve().parents[1] / "cloud-init" / "user-data.yaml.tpl"


def _render(values: dict[str, str]) -> str:
    """Mirror C1's runtime substitution (Jinja-free, by design — operators
    can inspect the template + substitute by hand if they need to debug."""
    text = TPL_PATH.read_text()
    for key, val in values.items():
        text = text.replace("{{ " + key + " }}", val)
    return text


@pytest.fixture
def sample_values() -> dict[str, str]:
    return {
        "AGENT_KIND": "goose",
        "AGENT_VERSION": "1.34.1",
        "AGENT_USER": "alice",
        "AGENT_USER_UID": "1234",
        "AGENT_REGISTRY_URL": "registry.customer.internal/agent-platform",
        "AGENT_MODEL": "qwen-coder-32b",
        "GATEWAY_URL": "http://agent-platform-llm-gateway.internal:4000",
        "AGENT_PLATFORM_USER_TOKEN": "tok_test_abc123",
        "HEARTBEAT_URL": "http://agent-platform-control.internal/v1/heartbeat",
        "TTYD_ALLOW_CIDR": "10.20.0.0/16",
    }


def test_template_exists() -> None:
    assert TPL_PATH.exists(), f"template missing at {TPL_PATH}"


def test_rendered_is_valid_yaml(sample_values: dict[str, str]) -> None:
    rendered = _render(sample_values)
    parsed = yaml.safe_load(rendered)
    assert isinstance(parsed, dict)
    assert "users" in parsed
    assert "write_files" in parsed
    assert "runcmd" in parsed


def test_users_section_creates_per_owner_account(sample_values: dict[str, str]) -> None:
    """Decision 1B: every VM has a per-owner linux account."""
    parsed = yaml.safe_load(_render(sample_values))
    users = parsed["users"]
    assert isinstance(users, list) and len(users) == 1
    user = users[0]
    assert user["name"] == sample_values["AGENT_USER"]
    assert str(user["uid"]) == sample_values["AGENT_USER_UID"]
    assert user["shell"] == "/bin/bash"
    assert user["create_home"] is True
    assert user["lock_passwd"] is True
    # sudo: false — sensitive ops go through portal admin, not local sudo
    assert user["sudo"] is False


def test_install_env_carries_all_plugin_inputs(sample_values: dict[str, str]) -> None:
    """install-agent.sh sources install.env; every plugin sees these vars."""
    parsed = yaml.safe_load(_render(sample_values))
    install_env = next(
        (f for f in parsed["write_files"] if f["path"] == "/etc/agent-platform/install.env"),
        None,
    )
    assert install_env is not None, "/etc/agent-platform/install.env not written"
    body = install_env["content"]
    for key, val in sample_values.items():
        assert f"{key}={val}" in body, f"{key} missing or wrong in install.env"
    # Computed field: AGENT_RUNTIME_ENV path is derived from AGENT_KIND
    assert "AGENT_RUNTIME_ENV=/etc/agent-platform/goose.env" in body


def test_install_env_is_root_only(sample_values: dict[str, str]) -> None:
    """Token-bearing file must be 0600 root:root."""
    parsed = yaml.safe_load(_render(sample_values))
    install_env = next(
        f for f in parsed["write_files"] if f["path"] == "/etc/agent-platform/install.env"
    )
    assert install_env["permissions"] == "0600"
    assert install_env["owner"] == "root:root"


def test_runcmd_invokes_generic_installer(sample_values: dict[str, str]) -> None:
    """Decision 2D: runcmd hits the generic dispatcher, not a goose-named one."""
    parsed = yaml.safe_load(_render(sample_values))
    cmds = parsed["runcmd"]
    assert any(
        "/opt/agent-platform/cloud-init/scripts/install-agent.sh"
        in (cmd if isinstance(cmd, str) else " ".join(cmd))
        for cmd in cmds
    )
    # The legacy "install-goose.sh" name must NOT appear — that file is now
    # a plugin sourced by install-agent.sh, not a runcmd target.
    raw = _render(sample_values)
    assert "install-goose.sh" not in raw


def test_no_unsubstituted_placeholders(sample_values: dict[str, str]) -> None:
    rendered = _render(sample_values)
    assert "{{ " not in rendered, "unsubstituted {{ ... }} placeholder remains"
    assert " }}" not in rendered


def test_ttyd_allow_cidr_supplied_lands_in_install_env(sample_values: dict[str, str]) -> None:
    """SEC-1: a deploy that supplies a trusted CIDR opens ttyd to that segment.
    The value must reach install.env verbatim — install-agent.sh sources it and
    passes it to `ufw allow from <cidr> ... port 7681`."""
    parsed = yaml.safe_load(_render(sample_values))
    body = next(
        f["content"]
        for f in parsed["write_files"]
        if f["path"] == "/etc/agent-platform/install.env"
    )
    assert "TTYD_ALLOW_CIDR=10.20.0.0/16" in body


def test_ttyd_allow_cidr_unset_renders_empty_fail_closed(sample_values: dict[str, str]) -> None:
    """SEC-1 fail-closed: a deploy with no trusted CIDR renders an empty value
    (`TTYD_ALLOW_CIDR=`), not a leftover placeholder. install-agent.sh then
    takes the else branch and leaves 7681 firewalled — never a blanket open."""
    values = {**sample_values, "TTYD_ALLOW_CIDR": ""}
    rendered = _render(values)
    parsed = yaml.safe_load(rendered)
    body = next(
        f["content"]
        for f in parsed["write_files"]
        if f["path"] == "/etc/agent-platform/install.env"
    )
    # Empty assignment present, on its own line — bash sees TTYD_ALLOW_CIDR="".
    assert "TTYD_ALLOW_CIDR=\n" in body or body.rstrip().endswith("TTYD_ALLOW_CIDR=")
    # No unsubstituted placeholder leaked through when the value is empty.
    assert "{{ TTYD_ALLOW_CIDR }}" not in rendered


def test_template_doesnt_leak_goose_specifics(sample_values: dict[str, str]) -> None:
    """Decision 2D: template body is agent-agnostic. Comments may list goose
    as one example plugin; actual config + commands must not name it."""
    raw = TPL_PATH.read_text()
    body_lines = [line for line in raw.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(body_lines)
    assert "goose" not in body.lower(), (
        f"template body still hard-codes 'goose'; offending lines:\n{body}"
    )
