"""Smoke tests for the ttyd web-terminal unit and its install hook (W-3.2).

These don't actually run ttyd; they assert that the systemd unit file is
well-formed and that ``install-agent.sh`` performs the install steps the
W-3.2 plan calls for. Real end-to-end verification (cloud-init clones a
template, ttyd listens on :7681, portal connects) needs the next vCenter
image build and isn't reachable from CI.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TTYD_UNIT = REPO_ROOT / "cloud-init" / "systemd" / "ttyd.service"
INSTALL_AGENT = REPO_ROOT / "cloud-init" / "scripts" / "install-agent.sh"


def _parse_unit(path: Path) -> configparser.ConfigParser:
    """configparser doesn't tolerate systemd's bare directive lines, but
    it's good enough for our key=value-style assertions if we set
    ``allow_no_value`` and feed it the file as-is."""
    cp = configparser.ConfigParser(strict=False, interpolation=None, allow_no_value=True)
    cp.read_string(path.read_text())
    return cp


@pytest.fixture
def unit() -> configparser.ConfigParser:
    return _parse_unit(TTYD_UNIT)


def test_ttyd_unit_listens_on_7681_with_write_enabled(unit):
    """``-W`` is required so the portal terminal accepts keystrokes; ``-p 7681``
    matches docs/architecture/29 §3.1 and the C1 ttyd-url shape."""
    exec_start = unit.get("Service", "ExecStart")
    assert "/usr/local/bin/ttyd" in exec_start
    assert "-p 7681" in exec_start
    assert "-W" in exec_start
    assert exec_start.rstrip().endswith("bash"), exec_start


def test_ttyd_unit_restarts_on_failure(unit):
    """A flaky ttyd process must not leave the portal terminal dead — the
    image is supposed to expose a working terminal for the lifetime of the VM."""
    assert unit.get("Service", "Restart") == "on-failure"
    # Bounded restart so a broken binary can't loop the host into 100% CPU.
    assert unit.get("Unit", "StartLimitBurst") == "5"


def test_ttyd_unit_has_hardening(unit):
    """Minimum hardening — full sandbox would block ttyd's pty needs."""
    assert unit.get("Service", "NoNewPrivileges") == "true"
    assert unit.get("Service", "PrivateTmp") == "true"


def test_install_agent_installs_and_enables_ttyd():
    body = INSTALL_AGENT.read_text()
    # ttyd block exists and is gated on the unit file being readable.
    assert "PKG_DIR}/systemd/ttyd.service" in body
    # apt path is preferred; upstream binary is the fallback.
    assert "apt-get install -y ttyd" in body
    assert "github.com/tsl0922/ttyd/releases/download" in body
    # The unit is dropped into /etc/systemd/system and enabled at boot.
    assert 'install -m 0644 "${PKG_DIR}/systemd/ttyd.service"' in body
    assert "systemctl enable --now ttyd.service" in body
    # SEC-1: ufw scopes 7681 to a configured trusted CIDR and fails closed when
    # unset — never a blanket `ufw allow 7681/tcp` to the whole network.
    assert "ufw allow 7681/tcp" not in body
    assert 'ufw allow from "${TTYD_ALLOW_CIDR}" to any port 7681 proto tcp' in body
    assert "TTYD_ALLOW_CIDR unset" in body  # fail-closed warning path
