#!/usr/bin/env bash
# =============================================================================
# 01-base-hardening.sh
# Base OS hardening for agent-platform gold image.
# =============================================================================
set -euo pipefail

echo "[01] Running base OS hardening..."

export DEBIAN_FRONTEND=noninteractive

# ── Disable unused services ───────────────────────────────────────────────────
for svc in snapd apparmor whoopsie ufw; do
  systemctl disable --now "$svc" 2>/dev/null || true
done

# ── SSH hardening ─────────────────────────────────────────────────────────────
sed -i 's/^#*PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#*PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd || systemctl restart ssh

# ── Audit logging ─────────────────────────────────────────────────────────────
mkdir -p /var/log/journal
journalctl --update-boot-catalog 2>/dev/null || true

# ── cloud-init network: respect vSphere DHCP ───────────────────────────────────
cat > /etc/netplan/99-cloud-init.yaml << 'NETPLAN'
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: true
NETPLAN
chmod 600 /etc/netplan/99-cloud-init.yaml
netplan apply 2>/dev/null || true

# ── Cleanup cloud-init build artifacts ───────────────────────────────────────
cloud-init clean --logs 2>/dev/null || true

# ── Clear machine-id (unique per clone) ──────────────────────────────────────
truncate -s 0 /etc/machine-id
ln -sf /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null || true

# ── Remove DHCP lease state ───────────────────────────────────────────────────
rm -f /var/lib/dhcp/dhclient.leases /var/lib/dhcp/dhclient6.leases
mkdir -p /var/lib/dhcp
touch /var/lib/dhcp/dhclient.leases

# ── Clean temp directories ────────────────────────────────────────────────────
rm -rf /tmp/* /var/tmp/*
mkdir -p /tmp /var/tmp
chmod 1777 /tmp /var/tmp

echo "[01] Hardening complete at $(date -u '+%Y-%m-%dT%H:%M:%SZ')" | tee /var/log/agent-platform-hardening.log
