#!/usr/bin/env bash
# ⚠️ DEPRECATED (2026-06-10, docs/architecture/35-2026-06-09-c1-c21-agent-install-convergence.md): plugin-contract agent install is FROZEN.
# 单一事实源已收敛到 C21 runcmd 注册表
# (agent-platform-web-ui/src/lib/providers/vsphere/cloudinit/agents.ts)，真机验证通过。
# 本脚本暂留：C1 VmwareProvisioner 仍渲染 user-data.yaml.tpl 调它（hands-on/pending
# 后端），待 C1→C21 后端收敛时连同 agent-plugins/ 一并删除。不要在此新增 agent。
#
# Agent Platform generic agent installer — invoked by cloud-init runcmd.
#
# Reads /etc/agent-platform/install.env, sources the agent plugin matching
# AGENT_KIND from /opt/agent-platform/agent-plugins/, and invokes its lifecycle
# functions in order.
#
# Plugin contract: see /opt/agent-platform/cloud-init/docs/plugin-interface.md
# (mirrored from packages/agent-platform-image/docs/plugin-interface.md at image
# build time).
#
# Designed to be idempotent: re-running on an already-configured VM is safe
# as long as each plugin function is idempotent (contract requirement).

set -euo pipefail

INSTALL_ENV=/etc/agent-platform/install.env
PLUGIN_DIR=/opt/agent-platform/agent-plugins
PKG_DIR=/opt/agent-platform/cloud-init
SYSTEMD_DIR=/etc/systemd/system

log() { echo "[install-agent] $*"; }
die() { echo "[install-agent] FATAL: $*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight

[[ -r "${INSTALL_ENV}" ]] || die "${INSTALL_ENV} missing or unreadable"

set -a
# shellcheck source=/dev/null
source "${INSTALL_ENV}"
set +a

: "${AGENT_KIND:?AGENT_KIND required in install.env}"
: "${AGENT_VERSION:?AGENT_VERSION required}"
: "${AGENT_USER:?AGENT_USER required}"
: "${AGENT_USER_UID:?AGENT_USER_UID required}"
: "${AGENT_REGISTRY_URL:?AGENT_REGISTRY_URL required}"
: "${GATEWAY_URL:?GATEWAY_URL required}"
: "${AGENT_PLATFORM_USER_TOKEN:?AGENT_PLATFORM_USER_TOKEN required}"
: "${HEARTBEAT_URL:?HEARTBEAT_URL required}"
: "${AGENT_RUNTIME_ENV:?AGENT_RUNTIME_ENV required}"

# Verify the per-VM owner account exists (cloud-init users: section should
# have created it). Fail loud if not — otherwise plugin_start's sudo -u dies
# with a less-helpful error later.
id -u "${AGENT_USER}" >/dev/null 2>&1 \
    || die "linux account '${AGENT_USER}' missing — check cloud-init users: section render"

# ---------------------------------------------------------------- plugin

PLUGIN_PATH="${PLUGIN_DIR}/${AGENT_KIND}.sh"
if [[ ! -r "${PLUGIN_PATH}" ]]; then
    if [[ -e "${PLUGIN_PATH}.placeholder" ]]; then
        die "agent_kind='${AGENT_KIND}' is a placeholder (not yet enabled). \
See docs/architecture/07-open-questions.md or unfreeze in plugin file."
    fi
    die "agent plugin not found at ${PLUGIN_PATH}. Known plugins: $(ls ${PLUGIN_DIR}/*.sh 2>/dev/null || echo '(none)')"
fi

log "loading plugin ${AGENT_KIND} from ${PLUGIN_PATH}"
# shellcheck source=/dev/null
source "${PLUGIN_PATH}"

# Verify the plugin exposes the 4 required functions.
for fn in plugin_install plugin_configure plugin_start plugin_healthcheck; do
    declare -f "${fn}" >/dev/null \
        || die "plugin ${AGENT_KIND} missing required function ${fn}() — see plugin-interface.md"
done

# ---------------------------------------------------------------- lifecycle

log "(1/4) plugin_install"
plugin_install

# TODO(PR-C): POST /api/cloud-init/exchange-token here using
# AGENT_PLATFORM_USER_TOKEN; receive secrets payload; write secret-bearing values
# into the runtime env file before plugin_configure runs. For now we trust
# install.env and the plugin will write a placeholder runtime env.

log "(2/4) plugin_configure"
plugin_configure

# The runtime env file (${AGENT_RUNTIME_ENV}) is written root-owned, but the
# agent runs as a systemd --user service under ${AGENT_USER} — and a --user
# manager reads EnvironmentFile with the user's privileges. A root:root 0600
# file is unreadable there, so the unit would fail "Failed to load environment
# files". chown it to the agent user here (common to every plugin) so the fix
# can't be forgotten per-plugin. See plugin-interface.md §2.2 (#280).
chown "${AGENT_USER}:${AGENT_USER}" "${AGENT_RUNTIME_ENV}"

# Enable-linger so the per-user systemd manager survives without an active
# login session — required for systemd --user services (decision 1B + 2D).
log "enable-linger for ${AGENT_USER}"
loginctl enable-linger "${AGENT_USER}" || \
    log "WARN: loginctl enable-linger failed (continuing — may already be set)"

log "(3/4) plugin_start"
plugin_start

# ---------------------------------------------------------------- heartbeat

# Heartbeat is system-level (not per-agent). Install once regardless of plugin.
if [[ -d "${PKG_DIR}/systemd" ]]; then
    install -m 0755 "${PKG_DIR}/scripts/agent-platform-heartbeat.sh" /usr/local/bin/agent-platform-heartbeat
    install -m 0644 "${PKG_DIR}/systemd/agent-platform-heartbeat.service" "${SYSTEMD_DIR}/agent-platform-heartbeat.service"
    install -m 0644 "${PKG_DIR}/systemd/agent-platform-heartbeat.timer"   "${SYSTEMD_DIR}/agent-platform-heartbeat.timer"
    systemctl daemon-reload
    systemctl enable --now agent-platform-heartbeat.timer
fi

# ---------------------------------------------------------------- ttyd (W-3.2)

# ttyd powers the portal Terminal page (docs/architecture/29 §3). Direct
# portal → VM:7681 wss — M1 trusts the NSX segment isolation, M2 adds an
# HMAC token sidecar via a W-3.3 follow-up. Idempotent: re-installing the
# unit is a no-op once it's already enabled.
if [[ -r "${PKG_DIR}/systemd/ttyd.service" ]]; then
    # Prefer apt ttyd on Ubuntu 22.04 universe (≥ 1.7.3); fall back to the
    # upstream prebuilt static binary on older releases or locked-down
    # templates where apt can't reach universe.
    if ! command -v ttyd >/dev/null 2>&1; then
        if apt-get install -y ttyd 2>/dev/null; then
            log "ttyd installed via apt"
        else
            TTYD_VERSION="${TTYD_VERSION:-1.7.7}"
            # Pin the expected sha256 of the upstream ttyd.x86_64 binary so a
            # root install never trusts an unverified download (this fallback
            # also contradicts the offline premise — but when it does run, it
            # must verify). The digest is the official 1.7.7 SHA256SUMS entry.
            # If TTYD_VERSION is overridden, TTYD_SHA256 must be supplied too or
            # we fail closed rather than skip verification.
            TTYD_SHA256="${TTYD_SHA256:-8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55}"
            log "apt install ttyd failed; falling back to upstream binary v${TTYD_VERSION}"
            tmp_ttyd="$(mktemp)"
            curl -fsSL -o "${tmp_ttyd}" \
                "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64"
            if ! echo "${TTYD_SHA256}  ${tmp_ttyd}" | sha256sum -c - >/dev/null 2>&1; then
                rm -f "${tmp_ttyd}"
                die "ttyd v${TTYD_VERSION} sha256 mismatch (expected ${TTYD_SHA256}). \
Pre-install ttyd in the image or set TTYD_SHA256 for the pinned version."
            fi
            install -m 0755 "${tmp_ttyd}" /usr/local/bin/ttyd
            rm -f "${tmp_ttyd}"
        fi
    fi
    install -m 0644 "${PKG_DIR}/systemd/ttyd.service" "${SYSTEMD_DIR}/ttyd.service"
    systemctl daemon-reload
    systemctl enable --now ttyd.service
    # SEC-1: ttyd binds 0.0.0.0:7681 and serves an UNAUTHENTICATED writable
    # shell (M2's W-3.3 HMAC sidecar is the real fix). Until then, ENFORCE the
    # M1 "trusted network" assumption instead of merely documenting it: only
    # open 7681 to the configured trusted source (the portal/control-plane NSX
    # segment). Fail closed — if TTYD_ALLOW_CIDR is unset we do NOT blanket-open
    # the port, so a misconfig can never expose a root-capable shell to the
    # whole routable network. ufw is inactive on minimal templates — silence
    # that failure rather than abort cloud-init.
    if command -v ufw >/dev/null 2>&1; then
        if [[ -n "${TTYD_ALLOW_CIDR:-}" ]]; then
            ufw allow from "${TTYD_ALLOW_CIDR}" to any port 7681 proto tcp \
                || log "WARN: ufw allow 7681 from ${TTYD_ALLOW_CIDR} failed (continuing)"
        else
            log "WARN: TTYD_ALLOW_CIDR unset — NOT opening 7681. ttyd stays" \
                "firewalled (no remote terminal) rather than exposing an" \
                "unauthenticated shell. Set TTYD_ALLOW_CIDR to the portal/" \
                "control-plane segment to enable the Terminal page."
        fi
    fi
fi

log "(4/4) plugin_healthcheck"
plugin_healthcheck || die "plugin_healthcheck failed — see plugin stderr above"

log "agent_kind=${AGENT_KIND} agent_version=${AGENT_VERSION} install complete"
