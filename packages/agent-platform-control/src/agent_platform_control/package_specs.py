"""VM package specs loader (decision 11).

Reads ``config/vm_package_specs.yaml`` once at startup and caches the parsed
result. Replaces the hard-coded ``_PACKAGE_TEMPLATE`` dict that lived in
``api/deployments.py`` for the M1 single-tenant special case.

Forward path (decision 11 → C, M2): move to a ``vm_package_specs`` DB table
maintained by an admin endpoint. The PackageSpec / specs() public surface
stays the same; only the loader implementation changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PackageSpec:
    """Resolved spec for one portal RequestAgent package choice."""

    name: str  # "agent-vm-small" etc — also the portal selector
    cpu: int
    memory_gb: int
    disk_gb: int
    template: str  # vCenter inventory path
    agent_kind: str  # "goose" | "qoder" | "xiaoguai" | ...
    agent_version: str
    agent_registry_url: str
    agent_model: str


@dataclass(frozen=True)
class DefaultQuota:
    vms_per_user: int


@dataclass(frozen=True)
class PackageSpecs:
    """Parsed view of the whole vm_package_specs.yaml file."""

    packages: dict[str, PackageSpec]
    default_quota: DefaultQuota

    def get(self, package_name: str) -> PackageSpec | None:
        return self.packages.get(package_name)

    def known_packages(self) -> list[str]:
        return sorted(self.packages.keys())


# Default location is alongside the agent-platform-control package; tests + alternate
# deployments override via the AGENT_PLATFORM_VM_PACKAGE_SPECS env var (resolved by
# Settings in config.py).
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "vm_package_specs.yaml"


class PackageSpecsError(ValueError):
    """Raised when vm_package_specs.yaml is missing or malformed."""


def _parse(raw: dict) -> PackageSpecs:
    pkgs_raw = raw.get("packages")
    if not isinstance(pkgs_raw, dict) or not pkgs_raw:
        raise PackageSpecsError("vm_package_specs.yaml must have a non-empty 'packages' mapping")

    packages: dict[str, PackageSpec] = {}
    for name, spec in pkgs_raw.items():
        try:
            packages[name] = PackageSpec(
                name=name,
                cpu=int(spec["cpu"]),
                memory_gb=int(spec["memory_gb"]),
                disk_gb=int(spec["disk_gb"]),
                template=str(spec["template"]),
                agent_kind=str(spec["agent_kind"]),
                agent_version=str(spec["agent_version"]),
                agent_registry_url=str(spec["agent_registry_url"]),
                agent_model=str(spec["agent_model"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PackageSpecsError(
                f"package '{name}' is missing or has invalid field: {exc}"
            ) from exc

    quota_raw = raw.get("default_quota", {})
    quota = DefaultQuota(
        vms_per_user=int(quota_raw.get("vms_per_user", 3)),
    )
    return PackageSpecs(packages=packages, default_quota=quota)


def load_specs(path: Path | None = None) -> PackageSpecs:
    """Parse the yaml file. Raises ``PackageSpecsError`` on any structural issue."""
    target = path or _DEFAULT_PATH
    if not target.is_file():
        raise PackageSpecsError(
            f"vm_package_specs.yaml not found at {target} — set AGENT_PLATFORM_VM_PACKAGE_SPECS"
        )
    with target.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise PackageSpecsError(
            f"vm_package_specs.yaml root must be a mapping, got {type(raw).__name__}"
        )
    return _parse(raw)


@lru_cache(maxsize=1)
def get_specs() -> PackageSpecs:
    """Cached default-path accessor.

    Tests call ``get_specs.cache_clear()`` after pointing at a fixture file via
    the ``AGENT_PLATFORM_VM_PACKAGE_SPECS`` env var (handled in Settings.config.py once
    that wiring lands; for now tests call ``load_specs(Path(...))`` directly).
    """
    return load_specs()
