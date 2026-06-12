# agent-platform-secrets (C18)

Vaultwarden + Caddy stack that backs the platform's secret store. Replaces every plaintext `password:` / `apiKey:` field in `config.yaml` with a vault lookup, and feeds short-lived tokens into agent VMs via cloud-init.

## Scope

| Task | Status |
|---|---|
| 1.20.1 Vaultwarden + Caddy bring-up | ✅ this PR |
| 1.20.2 Control-plane credential migration | ⬜ depends on 1.20.1 + C1 SDK wiring |
| 1.20.3 VM short-lived token exchange | ⬜ depends on 1.20.2 + C8 cloud-init |
| 1.20.4 Auto-reconnect on Vaultwarden restart | ✅ encoded in systemd unit (`Restart=always`, healthcheck-gated docker compose) |

## Bring-up

```bash
cd /opt/agent-platform-secrets   # production install path
cp .env.example .env && chmod 600 .env
# Edit .env — populate AGENT_PLATFORM_SECRETS_DOMAIN and VAULTWARDEN_ADMIN_TOKEN (Argon2id hash)
# Generate the admin token hash:
docker run --rm -it vaultwarden/server:1.32.4 /vaultwarden hash
# Start
sudo cp systemd/agent-platform-secrets.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-platform-secrets
# Verify
AGENT_PLATFORM_SECRETS_DOMAIN=$(grep ^AGENT_PLATFORM_SECRETS_DOMAIN .env | cut -d= -f2) ./scripts/healthcheck.sh
```

The admin panel is at `${AGENT_PLATFORM_SECRETS_DOMAIN}/admin` — use the plain admin token (not the hash) to log in. Create the first user via the panel, then disable signup (already set in compose).

## Security defaults

| Setting | Value | Reason |
|---|---|---|
| `SIGNUPS_ALLOWED` | `false` | accounts are admin-provisioned |
| `SENDS_ALLOWED` | `false` | the public "send" feature is not needed for an internal vault |
| `EMERGENCY_ACCESS_ALLOWED` | `false` | no third-party recovery contacts |
| `ADMIN_TOKEN` | Argon2id hash | plaintext token never stored on disk |
| Caddy TLS | `tls internal` (default) | issues from Caddy's local CA; swap for ACME when domain is public |
| HSTS / nosniff / X-Frame-Options | enabled in Caddyfile | transport-level hardening on top of Vaultwarden's own CSP |
| systemd | `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectKernelTunables`, etc. | minimum-privilege service unit |

## Image pinning

`docker-compose.yml` pins `vaultwarden/server:1.32.4` and `caddy:2.10.0-alpine`. For production, additionally pin by digest (`@sha256:...`) after validating each release. The pin policy follows the C9 `agent-platform-scale-bundle` cosign workflow once the image is mirrored into the local repo.

## Healthcheck

`scripts/healthcheck.sh` curls `/alive` via the public Caddy domain. The systemd unit's docker-compose healthcheck does the same against the internal container network. Both fail loud rather than silently degrading.

## Status

🟢 1.20.1 ready for review. 1.20.2 / 1.20.3 are tracked in the backlog and consume this stack via Vaultwarden's REST API.
