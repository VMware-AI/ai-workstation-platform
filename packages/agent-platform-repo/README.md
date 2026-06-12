# agent-platform-repo (C4)

Private PyPI for the Agent Platform platform. Wraps [devpi-server](https://devpi.net/)
so admins can publish signed scale bundles (C9) to an internal index that
tenant workstations consume with vanilla `pip` / `uv`.

> **Status** — M1 skeleton (Task 1.8). Init / upload / verify commands work
> against a local devpi; cosign verify, white-list rsync, and Caddy reverse
> proxy land in follow-up subtasks (1.8.1 → 1.8.4).

## Quickstart

```bash
# 1. Start devpi locally (binds to 127.0.0.1:3141 only).
export DEVPI_ROOT_PASSWORD='change-me'
docker compose up -d

# Bootstrap devpi state on first run.
docker compose exec devpi devpi-init

# 2. Install the CLI from the workspace.
uv sync
uv run agent-platform-repo --help

# 3. Create the root/agent-platform index.
export DEVPI_PASSWORD="$DEVPI_ROOT_PASSWORD"
uv run agent-platform-repo init

# 4. Upload a wheel.
uv run agent-platform-repo upload dist/example-0.1.0-py3-none-any.whl

# 5. Consume from any host on the LAN.
pip install \
  --index-url http://127.0.0.1:3141/root/agent-platform/+simple/ \
  example
```

## Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `DEVPI_URL` | `http://127.0.0.1:3141` | devpi-server base URL |
| `DEVPI_USER` | `root` | devpi user for write ops |
| `DEVPI_PASSWORD` | — | required for `init` / `upload` |
| `DEVPI_INDEX` | `root/agent-platform` | target index path |
| `DEVPI_ROOT_PASSWORD` | — | passed to the compose stack on bootstrap |

Credentials are read from the process environment only; Vaultwarden
(C18) integration is tracked under follow-up tasks.

## Development

```bash
cd packages/agent-platform-repo
uv sync
uv run pytest             # unit only — integration tests need a real devpi
uv run ruff check
uv run ruff format --check
uvx bandit -r src/
```

## Follow-up work (out of scope for this skeleton)

- **1.8.1** Caddy reverse proxy + TLS in front of devpi
- **1.8.2** rsync upstream PyPI white-list (air-gap mirror)
- **1.8.3** binary white-list cache (qcoder / Goose / Claude Code)
- **1.8.4** `uv tool install --index-url` validation with public PyPI blocked
- Wire `verify` to `agent-platform-scale-bundle` cosign chain (C9)
- Swap env-var credentials for Vaultwarden lookups (C18)
- Real integration test once C8 installer can stand up devpi in CI
