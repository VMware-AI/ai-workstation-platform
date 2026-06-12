#!/usr/bin/env bash
# W-3.1 — local ttyd backend so the portal Terminal page has something
# real to attach to without a vCenter or even a VM.
#
# Pairs with AGENT_PLATFORM_TTYD_MOCK_URL=ws://localhost:7681/ws on C1.
set -euo pipefail

NAME=ap-ttyd-mock
PORT=7681

if docker ps --format '{{.Names}}' | grep -q "^${NAME}$"; then
  echo "[ttyd-mock] already running on :${PORT}"
  exit 0
fi

# Remove any stopped container of the same name (--rm at run-time doesn't
# survive crashes); -W lets the xterm session write to the shell, which is
# the whole point of the demo.
docker rm -f "${NAME}" 2>/dev/null || true

docker run -d \
  --name "${NAME}" \
  -p "${PORT}:7681" \
  tsl0922/ttyd:latest \
  ttyd -p 7681 -W bash

echo "[ttyd-mock] up on ws://localhost:${PORT}/ws"
echo "[ttyd-mock] export AGENT_PLATFORM_TTYD_MOCK_URL=ws://localhost:${PORT}/ws and restart C1"
