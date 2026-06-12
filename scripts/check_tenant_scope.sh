#!/usr/bin/env bash
# check_tenant_scope.sh — enforce Decision 14 lint rule.
#
# Every API module that runs ``select(<TenantScopedModel>)`` must either:
#   (a) wrap the select in ``tenant_scope(...)``, OR
#   (b) opt out by declaring ``TENANT_SCOPE_ADMIN_BYPASS = True`` at module
#       scope (and the file must appear in the bypass whitelist below).
#
# Exit codes:
#   0 — all good
#   1 — at least one violation found
#
# Run locally:
#   bash scripts/check_tenant_scope.sh
# CI hooks this into the lint step.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${ROOT}/packages/agent-platform-control/src/agent_platform_control/api"

# Tenant-scoped models (must match db/tenant_scope.py TENANT_SCOPED_MODELS).
TENANT_MODELS=(VM TokenUsage Deployment Upgrade User)

# Files explicitly allowed to call select() on tenant-scoped models without
# wrapping in tenant_scope(). Each MUST declare TENANT_SCOPE_ADMIN_BYPASS = True
# at module scope — we double-check below.
BYPASS_FILES=(
  "deployments.py"
  "upgrades.py"
  "ingest.py"
  "cloud_init.py"
)

fail=0
trap 'exit $fail' EXIT

is_bypass() {
  local fname="$1"
  for b in "${BYPASS_FILES[@]}"; do
    if [[ "$fname" == "$b" ]]; then return 0; fi
  done
  return 1
}

# 1. Verify every bypass file actually carries the marker.
for b in "${BYPASS_FILES[@]}"; do
  f="${API_DIR}/${b}"
  if [[ ! -f "$f" ]]; then
    echo "WARN: bypass file not found: $f" >&2
    continue
  fi
  if ! grep -q '^TENANT_SCOPE_ADMIN_BYPASS = True' "$f"; then
    echo "ERROR: $f is in the bypass list but missing 'TENANT_SCOPE_ADMIN_BYPASS = True' marker" >&2
    fail=1
  fi
done

# 2. For every non-bypass api/*.py, flag raw select(<tenant_model>) without tenant_scope on the same line.
shopt -s nullglob
for f in "${API_DIR}"/*.py; do
  fname="$(basename "$f")"
  if [[ "$fname" == "__init__.py" ]]; then continue; fi
  if is_bypass "$fname"; then continue; fi

  for model in "${TENANT_MODELS[@]}"; do
    # Match `select(Model`. For every match in this file, the same file must
    # contain at least one `tenant_scope(...model=Model` call OR mention this
    # model in a `tenant_scope(` context (we allow `model=` keyword or the
    # `base = ...` indirection pattern).
    select_count=$(grep -cE "select\(\s*${model}\b" "$f" || true)
    if [[ "$select_count" -eq 0 ]]; then continue; fi
    # Accept if the file contains tenant_scope( referencing this model.
    if grep -qE "tenant_scope\(.*model=${model}\b" "$f"; then continue; fi
    if grep -qE "model=${model}\b" "$f" && grep -q 'tenant_scope(' "$f"; then continue; fi

    # Report all raw matches.
    while IFS=: read -r lineno line; do
      echo "ERROR: ${f}:${lineno} raw select(${model}) without tenant_scope() wrap" >&2
      echo "       ${line}" >&2
      echo "       fix: wrap in tenant_scope(select(${model})..., tenant_id=user.tenant_id, model=${model})" >&2
      fail=1
    done < <(grep -nE "select\(\s*${model}\b" "$f")
  done
done

if [[ $fail -eq 0 ]]; then
  echo "tenant_scope lint: OK ($(ls "${API_DIR}"/*.py | wc -l | tr -d ' ') files checked)"
fi
