#!/usr/bin/env bash
# dev-up.sh — start C1 + console in background.
# Logs to .dev-logs/{c1,console}.log. PIDs to .dev-logs/{c1,console}.pid.
# (C12 portal removed from the dev chain — doc 31; user surface is the agent's own web UI.)
# Stop with `make dev-down` or `bash scripts/dev-down.sh`.
#
# Per doc 23: bash + trap, no `concurrently` dep.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/.dev-logs"
mkdir -p "${LOG_DIR}"

# Cleanup on exit (Ctrl+C while attached)
cleanup() {
  echo "dev-up: caught signal, stopping services…"
  bash "${REPO_ROOT}/scripts/dev-down.sh" || true
  exit 0
}
trap cleanup INT TERM

# --- C1 control plane ---
echo "dev-up: starting agent-platform-control on :8000 …"
(
  cd "${REPO_ROOT}/packages/agent-platform-control"
  PYTHONPATH="src:../agent-platform-approval/src:../agent-platform-secrets/src" \
    uv run --no-project \
      --with 'fastapi>=0.115' \
      --with 'sqlalchemy[asyncio]>=2.0' \
      --with aiosqlite \
      --with 'pydantic>=2.9' \
      --with pydantic-settings \
      --with 'python-jose[cryptography]' \
      --with 'cryptography>=42' \
      --with httpx \
      --with 'pyvmomi>=8.0' \
      --with uvicorn \
      --with 'typer>=0.12' \
      uvicorn agent_platform_control.app:create_app --factory --port 8000 \
      > "${LOG_DIR}/c1.log" 2>&1 &
  echo $! > "${LOG_DIR}/c1.pid"
)
sleep 2  # rough warmup so vite proxy doesn't 502 on first paint

# --- C2 console ---
echo "dev-up: starting agent-platform-console on :5173 …"
(
  cd "${REPO_ROOT}/packages/agent-platform-console"
  pnpm dev > "${LOG_DIR}/console.log" 2>&1 &
  echo $! > "${LOG_DIR}/console.pid"
)

echo ""
echo "dev-up: both services started."
echo ""
echo "  C1 control:  http://localhost:8000  (logs: ${LOG_DIR}/c1.log)"
echo "  C2 console:  http://localhost:5173  (logs: ${LOG_DIR}/console.log)"
echo ""
echo "Stop with:  make dev-down"
echo "Tail logs:  tail -f ${LOG_DIR}/*.log"
