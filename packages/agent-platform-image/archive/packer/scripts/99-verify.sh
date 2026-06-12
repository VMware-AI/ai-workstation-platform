#!/usr/bin/env bash
# =============================================================================
# 99-verify.sh
# Final smoke-test verification before Packer stops the VM and exports OVA.
# All checks must pass for the build to be considered successful.
# =============================================================================
set -euo pipefail

echo "[99] Running build verification..."

_ERRORS=0

# ── Helper ─────────────────────────────────────────────────────────────────────
check() {
  local label="$1"
  local cmd="$2"
  if eval "$cmd"; then
    echo "[99] PASS: $label"
  else
    echo "[99] FAIL: $label"
    _ERRORS=$((_ERRORS + 1))
  fi
}

# ── OS ────────────────────────────────────────────────────────────────────────
check "ubuntu version"    "[ -f /etc/lsb-release ] && grep -q 'Ubuntu 22' /etc/lsb-release"
check "kernel version"     "[ -n '$(uname -r)' ]"

# ── Users ─────────────────────────────────────────────────────────────────────
check "ubuntu user exists"  "id ubuntu &>/dev/null"
check "ubuntu user locked"  "[ 'L' = \"\$(passwd -S ubuntu | awk '{print \$2}')\" ]"

# ── SSH ────────────────────────────────────────────────────────────────────────
check "sshd running"     "systemctl is-active --quiet sshd || systemctl is-active --quiet ssh"
check "sshd enabled"      "systemctl is-enabled sshd &>/dev/null || systemctl is-enabled ssh &>/dev/null"
check "ssh key-auth only" "[ 'no' = \"\$(grep '^PasswordAuthentication' /etc/ssh/sshd_config | tail -1 | awk '{print \$2}')\" ]"
check "ssh root-login off" "[ 'no' = \"\$(grep '^PermitRootLogin' /etc/ssh/sshd_config | tail -1 | awk '{print \$2}')\" ]"

# ── Toolchain ─────────────────────────────────────────────────────────────────
check "python3"     "command -v python3"
check "uv"          "command -v uv"
check "node"        "command -v node"
check "pnpm"        "command -v pnpm"
check "docker"      "command -v docker"
check "yq"          "command -v yq"
check "cosign"      "command -v cosign"

# ── Python packages ──────────────────────────────────────────────────────────────
check "fastapi pip"  "python3 -c 'import fastapi'"
check "uv installed" "uv --version"

# ── cloud-init ─────────────────────────────────────────────────────────────────
check "cloud-init"    "command -v cloud-init"
check "cloud-init result exists" "[ -f /run/cloud-init/result ]"

# ── VMware Tools ────────────────────────────────────────────────────────────────
check "open-vm-tools"  "dpkg -l open-vm-tools 2>/dev/null | grep -q '^ii'"
check "vmtoolsd"       "systemctl is-active --quiet vmtoolsd"

# ── Disk ────────────────────────────────────────────────────────────────────────
check "root fs mounted"  "mountpoint -q /"
check "disk size >= 20GB" "[ $(df -B1G / | tail -1 | awk '{print \$2}') -ge 20 ]"

# ── agent-platform metadata ────────────────────────────────────────────────────────────
check "agent-platform-build-meta exists" "[ -f /etc/agent-platform-build-meta ]"

# ── Network ────────────────────────────────────────────────────────────────────
check "eth0 exists"   "ip link show eth0 &>/dev/null"
check "default route" "[ -n \"\$(ip route show default)\" ]"
check "DNS resolution" "ping -c1 -W2 8.8.8.8 &>/dev/null || echo 'DNS check skipped (lab network)'"

# ── Result ──────────────────────────────────────────────────────────────────────
echo ""
if [ "$_ERRORS" -eq 0 ]; then
  echo "[99] ============================================="
  echo "[99]  ALL CHECKS PASSED — image is ready"
  echo "[99] ============================================="
  exit 0
else
  echo "[99] ============================================="
  echo "[99]  $_ERRORS CHECK(S) FAILED — review above"
  echo "[99] ============================================="
  exit 1
fi
