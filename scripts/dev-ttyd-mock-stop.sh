#!/usr/bin/env bash
# W-3.1 — stop the local ttyd mock container started by dev-ttyd-mock.sh.
set -uo pipefail

NAME=ap-ttyd-mock

if docker ps --format '{{.Names}}' | grep -q "^${NAME}$"; then
  docker stop "${NAME}" >/dev/null
  echo "[ttyd-mock] stopped"
else
  echo "[ttyd-mock] not running"
fi

# Best-effort cleanup of stopped/exited container of the same name.
docker rm -f "${NAME}" 2>/dev/null || true
