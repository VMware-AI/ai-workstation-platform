"""Tests for package_specs loader (decision 11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_platform_control.package_specs import (
    PackageSpecsError,
    load_specs,
)

# ---------------------------------------------------------- helpers


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "vm_package_specs.yaml"
    p.write_text(body, encoding="utf-8")
    return p


_VALID = """\
packages:
  agent-vm-small:
    cpu: 2
    memory_gb: 4
    disk_gb: 50
    template: "[t] s.vmtx"
    agent_kind: goose
    agent_version: "1.34.1"
    agent_registry_url: "registry/agent-platform"
    agent_model: "qwen-coder-32b"

  agent-vm-large:
    cpu: 8
    memory_gb: 16
    disk_gb: 200
    template: "[t] l.vmtx"
    agent_kind: goose
    agent_version: "1.34.1"
    agent_registry_url: "registry/agent-platform"
    agent_model: "qwen-coder-32b"

default_quota:
  vms_per_user: 5
"""


# ---------------------------------------------------------- happy path


def test_load_valid_yaml(tmp_path: Path) -> None:
    specs = load_specs(_write_yaml(tmp_path, _VALID))
    assert specs.known_packages() == ["agent-vm-large", "agent-vm-small"]

    small = specs.get("agent-vm-small")
    assert small is not None
    assert small.cpu == 2
    assert small.memory_gb == 4
    assert small.disk_gb == 50
    assert small.template == "[t] s.vmtx"
    assert small.agent_kind == "goose"
    assert small.agent_version == "1.34.1"
    assert small.agent_registry_url == "registry/agent-platform"
    assert small.agent_model == "qwen-coder-32b"


def test_default_quota_loaded(tmp_path: Path) -> None:
    specs = load_specs(_write_yaml(tmp_path, _VALID))
    assert specs.default_quota.vms_per_user == 5


def test_default_quota_uses_default_when_absent(tmp_path: Path) -> None:
    body = "\n".join(
        line
        for line in _VALID.splitlines()
        if "vms_per_user" not in line and "default_quota" not in line
    )
    specs = load_specs(_write_yaml(tmp_path, body))
    assert specs.default_quota.vms_per_user == 3


def test_get_unknown_package_returns_none(tmp_path: Path) -> None:
    specs = load_specs(_write_yaml(tmp_path, _VALID))
    assert specs.get("nonexistent") is None


# ---------------------------------------------------------- error paths


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PackageSpecsError, match="not found"):
        load_specs(tmp_path / "does-not-exist.yaml")


def test_root_must_be_mapping(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(PackageSpecsError, match="must be a mapping"):
        load_specs(p)


def test_packages_must_be_present(tmp_path: Path) -> None:
    with pytest.raises(PackageSpecsError, match="non-empty 'packages'"):
        load_specs(_write_yaml(tmp_path, "default_quota:\n  vms_per_user: 3\n"))


def test_package_missing_field_raises(tmp_path: Path) -> None:
    incomplete = """\
packages:
  agent-vm-small:
    cpu: 2
    memory_gb: 4
    # disk_gb missing
    template: "[t] s.vmtx"
    agent_kind: goose
    agent_version: "1.34.1"
    agent_registry_url: "registry/agent-platform"
    agent_model: "qwen-coder-32b"
"""
    with pytest.raises(PackageSpecsError, match=r"agent-vm-small.*missing or has invalid"):
        load_specs(_write_yaml(tmp_path, incomplete))


def test_real_config_file_loads() -> None:
    """The committed default config file must always parse cleanly."""
    from agent_platform_control.package_specs import get_specs

    get_specs.cache_clear()
    specs = get_specs()
    assert "agent-vm-small" in specs.known_packages()
    assert "agent-vm-gpu" in specs.known_packages()
    assert specs.default_quota.vms_per_user >= 1
