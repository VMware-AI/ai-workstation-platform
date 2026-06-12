#!/usr/bin/env bash
# =============================================================================
# 00-wait-cloud-init.sh
# Wait for cloud-init to finish before running application setup scripts.
# This prevents races where package installs fail because cloud-init is still
# running for the first time.
# =============================================================================
set -euo pipefail

echo "[00] Waiting for cloud-init to complete..."

# Method 1: wait for /run/cloud-init/result (most reliable on Ubuntu 22.04)
if [[ -f /run/cloud-init/result ]]; then
  result=$(cat /run/cloud-init/result)
  if [[ "$result" == "ready" ]]; then
    echo "[00] cloud-init finished successfully."
  else
    echo "[00] WARNING: cloud-init finished with result: $result"
  fi
else
  # Method 2: use cloud-init status --wait (blocks until done)
  echo "[00] /run/cloud-init/result not found, using cloud-init status --wait..."
  cloud-init status --wait 2>&1 || {
    echo "[00] WARNING: cloud-init status --wait returned non-zero. Continuing anyway."
  }
fi

# Method 3: also wait for the cloud-init.service to be deactivated
echo "[00] Waiting for cloud-init.service to stop..."
while systemctl is-active --quiet cloud-init.service 2>/dev/null; do
  echo "[00] cloud-init.service still active, waiting..."
  sleep 5
done
echo "[00] cloud-init.service is stopped."

echo "[00] cloud-init phase complete. Proceeding with provisioning."
