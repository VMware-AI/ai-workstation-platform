#!/usr/bin/env bash
# Synchronous healthcheck for agent-platform-secrets — used by smoke tests and
# the systemd ExecStartPost gate. Exits 0 only when Vaultwarden's
# /alive endpoint returns 200 *through Caddy*, i.e. the full chain
# (Caddy → vaultwarden container → SQLite) is up.

set -euo pipefail

: "${AGENT_PLATFORM_SECRETS_DOMAIN:?AGENT_PLATFORM_SECRETS_DOMAIN must be set}"

# Strip scheme — Caddy listens on both schemes, /alive only needs https.
url="${AGENT_PLATFORM_SECRETS_DOMAIN%/}/alive"

# SEC-19: verify the cert by default so this script can't be copied into a
# monitor that silently trusts any issuer. `tls internal` (Caddy local CA)
# bring-up sets HEALTHCHECK_INSECURE=1 to opt into -k; better still, point
# HEALTHCHECK_CACERT at Caddy's root so the check stays verifying.
curl_tls=()
if [[ -n "${HEALTHCHECK_CACERT:-}" ]]; then
    curl_tls=(--cacert "$HEALTHCHECK_CACERT")
elif [[ "${HEALTHCHECK_INSECURE:-0}" == "1" ]]; then
    curl_tls=(-k)
fi

if curl --silent --show-error --fail --max-time 10 "${curl_tls[@]}" "$url" >/dev/null; then
    echo "[ok] $url responded 200"
    exit 0
fi

echo "[fail] $url did not respond" >&2
exit 1
