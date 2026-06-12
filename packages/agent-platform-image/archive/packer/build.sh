#!/usr/bin/env bash
# =============================================================================
# build.sh — Build agent-platform-base OVA
# Usage: ./build.sh [var-file]
#   Default var-file: ./var-secrets.pkrvars.hcl
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAR_FILE="${1:-${SCRIPT_DIR}/var-secrets.pkrvars.hcl}"

if [[ ! -f "$VAR_FILE" ]]; then
  echo "ERROR: Variable file not found: $VAR_FILE"
  echo "Copy var-secrets.example.pkrvars.hcl and fill in real values."
  exit 1
fi

echo "============================================"
echo "Building agent-platform-base OVA"
echo "Var file: $VAR_FILE"
echo "============================================"

packer init .
packer build -var-file="$VAR_FILE" .

echo ""
echo "OVA built. To import into vSphere:"
echo "  1. Datastore browser → upload .ova"
echo "  2. Or: govc import.ova -name agent-platform-base-{DATE}.ova"
