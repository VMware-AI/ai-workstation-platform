#!/usr/bin/env bash
# dev-down.sh — stop services started by dev-up.sh.
# PID-first kill; name-pattern fallback (pkill) for orphaned processes.

set -uo pipefail  # no -e: we want to attempt all kills even if some fail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/.dev-logs"

kill_pidfile() {
  local name="$1"
  local pidfile="${LOG_DIR}/${name}.pid"
  if [ -f "${pidfile}" ]; then
    local pid
    pid="$(cat "${pidfile}")"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      echo "dev-down: stopping ${name} (pid ${pid})…"
      kill "${pid}" 2>/dev/null || true
      sleep 1
      # SIGKILL if still alive
      kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${pidfile}"
  fi
}

# PID-first
for svc in c1 console; do
  kill_pidfile "${svc}"
done

# Name-fallback for orphans (only matches our exact processes; safe)
pkill -f "uvicorn agent_platform_control.app" 2>/dev/null || true
pkill -f "vite.*agent-platform-console" 2>/dev/null || true

echo "dev-down: all services stopped."
