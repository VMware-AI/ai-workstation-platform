# agent-platform-image / cloud-init

Per-user VM bootstrap that installs **whichever agent `AGENT_KIND` selects** via
a generic dispatcher + per-agent plugin. Loaded into the C3 golden image at
`/opt/agent-platform/cloud-init/`; the userdata template is rendered by C1
agent-platform-control at clone time.

> ⚠️ **Plugin-contract install is FROZEN / deprecated (2026-06-10, doc 35).**
> The single source of truth for agent install has converged onto the **C21
> runcmd registry** (`agent-platform-web-ui/src/lib/providers/vsphere/cloudinit/agents.ts`).
> `install-agent.sh` + `agent-plugins/` survive only because C1's
> `VmwareProvisioner` still renders `user-data.yaml.tpl` (hands-on / pending
> backend). Add a new agent by editing the C21 registry — **not** by writing a
> new plugin here. See `agent-plugins/DEPRECATED.md` and `docs/plugin-interface.md`.

## Flow

```
        C1 clone VM (CloneVM_Task)
                ↓ inject user-data + OVF properties
        cloud-init write_files → /etc/agent-platform/install.env
        cloud-init runcmd
                ↓
        install-agent.sh            # generic dispatcher (agent-agnostic)
                ├─ source /etc/agent-platform/install.env
                ├─ source agent-plugins/${AGENT_KIND}.sh   (fail closed if missing/.placeholder)
                ├─ plugin_install      → install binary / docker image / venv
                ├─ plugin_configure    → write ${AGENT_RUNTIME_ENV} (/etc/agent-platform/<kind>.env)
                ├─ chown runtime env to ${AGENT_USER} + loginctl enable-linger
                ├─ plugin_start        → systemd --user agent.service
                ├─ install heartbeat   (system-level timer, once, any agent)
                ├─ install ttyd        (SEC-1: fail-closed on TTYD_ALLOW_CIDR)
                └─ plugin_healthcheck  → wait for agent.service active
```

## cloud-init userdata variables (`install.env`)

C1 fills these by substituting `{{ KEY }}` in `user-data.yaml.tpl`; they land in
`/etc/agent-platform/install.env` (0600, root:root) and are sourced by
`install-agent.sh`:

| Variable | Required | Example |
|---|:---:|---|
| `AGENT_KIND` | ✅ | `goose` \| `xiaoguai` \| `qoder` … (plugin selector) |
| `AGENT_VERSION` | ✅ | plugin-specific version / docker tag, e.g. `1.34.1` |
| `AGENT_USER` | ✅ | linux account name on the VM |
| `AGENT_USER_UID` | ✅ | numeric uid of that account (for `/run/user/<uid>`) |
| `AGENT_REGISTRY_URL` | ✅ | `registry.customer.internal/agent-platform` |
| `GATEWAY_URL` | ✅ | `http://agent-platform-llm-gateway.internal:4000` |
| `AGENT_PLATFORM_USER_TOKEN` | ✅ | per-user bearer token (C1 / C5 / C18) |
| `HEARTBEAT_URL` | ✅ | `http://agent-platform-control.internal/v1/heartbeat` |
| `AGENT_RUNTIME_ENV` | ✅ | `/etc/agent-platform/<AGENT_KIND>.env` (per-agent runtime env path) |
| `AGENT_MODEL` | optional | default per plugin (goose: `qwen-coder-32b`) |
| `TTYD_ALLOW_CIDR` | optional | SEC-1 trusted segment CIDR ttyd:7681 opens to; empty ⇒ ttyd stays firewalled (fail closed) |

## Files baked into the golden image

```
/opt/agent-platform/cloud-init/
├── scripts/
│   ├── install-agent.sh           # runcmd entrypoint (generic dispatcher)
│   ├── goose-launch.sh            # legacy goose ExecStart helper
│   └── agent-platform-heartbeat.sh   # timer-driven stub (full impl in monitoring PR)
├── systemd/
│   ├── goose-agent.service
│   ├── ttyd.service
│   ├── agent-platform-heartbeat.service
│   └── agent-platform-heartbeat.timer
├── docs/plugin-interface.md       # plugin contract (mirrored at image build)
└── README.md                      # this file

/opt/agent-platform/agent-plugins/
├── goose.sh                       # goose plugin (4 lifecycle functions)
├── goose-launch.sh                # launcher systemd --user invokes (docker vs tarball)
├── xiaoguai.sh                    # xiaoguai plugin
├── qoder.sh.placeholder           # not yet enabled → install-agent.sh fails closed
└── DEPRECATED.md                  # why this contract is frozen (doc 35)

/opt/goose-fallback/goose          # tarball binary (Packer adds this in C3 image build)
```

## Plugin contract

Each agent is one file `agent-plugins/${AGENT_KIND}.sh` exposing four shell
functions (0 args, env-var input, 0 = success): `plugin_install`,
`plugin_configure`, `plugin_start`, `plugin_healthcheck`. `install-agent.sh`
sources the file, verifies all four exist, and runs them in order. An unknown
`AGENT_KIND` (no `<kind>.sh`, or only a `.placeholder`) **fails closed** with an
educational error to the systemd journal rather than degrading. Full spec:
`docs/plugin-interface.md`.

## Docker vs tarball decision (goose plugin)

This lives in `agent-plugins/goose.sh` → `plugin_install`, not in the
dispatcher. goose picks `docker` when both:

1. `docker` binary on PATH **and** `docker info` succeeds (daemon reachable)
2. `docker pull ${AGENT_REGISTRY_URL}/goose:${AGENT_VERSION}` returns 0

Otherwise it falls back to the tarball binary at `/opt/goose-fallback/goose`. If
the tarball is also missing, `plugin_install` returns non-zero and the VM clone
is considered broken. The tarball is shipped in the C3 Packer image so the
customer's local docker registry is **not** a hard requirement for first boot.

## Heartbeat

`agent-platform-heartbeat.timer` is installed by `install-agent.sh` (system
level, once, regardless of agent) and fires every 60s, POSTing a JSON line to
`HEARTBEAT_URL`. The script sources `${AGENT_RUNTIME_ENV}` and checks
`${AGENT_HEARTBEAT_SERVICE:-goose-agent.service}`; it never fails the timer —
transient network errors are swallowed. Full payload (CPU / mem / token
billing) lands in the monitoring PR; current payload:

```json
{"ts":"2026-05-20T12:34:56Z","host":"agent-vm-001","mode":"docker","goose_active":"active"}
```

## ttyd (W-3.2)

`install-agent.sh` installs `ttyd.service` (powers the portal Terminal page).
**SEC-1**: ttyd binds `0.0.0.0:7681` and serves an unauthenticated writable
shell, so the install only opens port 7681 to `TTYD_ALLOW_CIDR` (the
portal/control-plane NSX segment) via ufw. If `TTYD_ALLOW_CIDR` is empty, the
port stays firewalled (fail closed — no remote terminal, never an exposed
shell). The real fix (M2 W-3.3) puts an HMAC token sidecar in front of ttyd.

## Local dev / debugging

Render the template manually:

```bash
cd packages/agent-platform-image
uvx --with pyyaml --with pytest pytest tests/ -v
```

On a real VM, inspect the install:

```bash
sudo journalctl -u ttyd.service -n 200 --no-pager
sudo -u <AGENT_USER> XDG_RUNTIME_DIR=/run/user/<AGENT_USER_UID> \
    systemctl --user status agent.service
sudo cat /etc/agent-platform/install.env        # 0600, root-only
sudo cat /etc/agent-platform/<AGENT_KIND>.env   # 0600, runtime env
```

To force re-install (idempotent):

```bash
sudo /opt/agent-platform/cloud-init/scripts/install-agent.sh
```

## Customer mirror seed

See `packages/agent-platform-image/agents/goose/README.md` for how to mirror the goose
image from GHCR into `${AGENT_REGISTRY_URL}`.
