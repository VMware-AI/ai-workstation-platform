// Agent install registry (doc 33 §4) — maps a known agent type to the shell
// commands appended to the cloud-init `runcmd` tail. Two sources feed runcmd:
//   1. a known agentType → this registry's command group, OR
//   2. agentType left empty → admin-supplied free-form install commands.
//
// Initial coverage = goose + xiaoguai (doc 33 §9 OQ-2). The C3 golden image is
// expected to pre-bake common deps (docker/python/open-vm-tools) so these only
// install the agent-specific delta.
//
// OFFLINE INSTALL (#286, doc 33 §4.1): customer VMs boot with NO public
// network / DNS, so neither github.com nor PyPI is reachable. Both agents are
// pulled as self-contained tar archives from an operator-hosted internal
// mirror, whose root comes from AGENT_PKG_BASE_URL (HTTP/NAS/FTP). goose is a
// system install (/usr/local/bin) with a sha256 gate; xiaoguai is a Rust
// single-binary that installs into the user's home (~/.local/bin) and so must
// `su` to the deploy user via the {{AGENT_USER}} placeholder (doc 33 §4.1).

// xiaoguai first — it is the preferred default agent (doc c20 §2026-06-08).
export const KNOWN_AGENT_TYPES = ["xiaoguai", "goose"] as const;
export type AgentType = (typeof KNOWN_AGENT_TYPES)[number];

// Placeholders substituted at generate time, SERVER-SIDE only, in
// cloudinit/index.ts (where env + osUser are known). This registry stays pure
// so it is safe to call from the browser deploy form (which expands it into the
// install-commands textarea) — no env read here, so it never throws there.
//
/** → the deploy `osUser`. Home-dir installs reference it to `su` to the right
 *  user; substitution + username validation live in cloudinit/index.ts. */
export const AGENT_USER_TOKEN = "{{AGENT_USER}}";
/** → the internal package mirror root (AGENT_PKG_BASE_URL). Customer VMs have
 *  no egress, so binaries are pulled from an operator-hosted mirror; the env
 *  read + URL validation live in cloudinit/index.ts (#286). */
export const AGENT_PKG_BASE_URL_TOKEN = "{{AGENT_PKG_BASE_URL}}";
/** → the sha256 of the mirrored xiaoguai artifact (AGENT_XIAOGUAI_SHA256).
 *  Unlike goose's compile-time-pinned GOOSE_SHA256, the xiaoguai artifact is
 *  whatever the operator mirrored, so its hash is injected server-side (env
 *  read + 64-hex validation live in cloudinit/index.ts). Fail-closed: a
 *  xiaoguai install with this env unset throws rather than skipping the gate. */
export const AGENT_XIAOGUAI_SHA256_TOKEN = "{{XIAOGUAI_SHA256}}";

// Goose release pin (#228): no pipe-to-shell, no floating "stable" tag. The
// tarball is sha256-verified before anything from it executes; with the
// offline mirror the hash also pins the bytes (and thus the version) even
// though the version no longer appears in the URL path. Hash computed
// 2026-06-07 from two independent download paths of the official release asset.
//
// Pinned to goose v1.37.0; to bump, mirror the new asset then update
// GOOSE_SHA256 —  curl -fsSL <url> | shasum -a 256
const GOOSE_SHA256 = "aa8042b7945e0f100276f01c22838039a50cc3ae6cd11fe0f1e0eefb900c2067";
const GOOSE_TARBALL = "goose-x86_64-unknown-linux-gnu.tar.bz2";

// xiaoguai (小怪) release pin: a self-contained Rust binary (no Python needed),
// mirrored as a .tar.gz. To bump: mirror the new asset + update this version.
//
// Supply-chain: the tarball is sha256-verified before anything from it runs,
// mirroring goose. The hash itself is injected server-side ({{XIAOGUAI_SHA256}}
// → AGENT_XIAOGUAI_SHA256) rather than compile-time-pinned, because the artifact
// is whatever the operator mirrored. A mirror poison / MITM that swaps the bytes
// fails the gate and aborts the install (the &&-chain stops before tar/exec).
const XIAOGUAI_VERSION = "1.13.0";
const XIAOGUAI_TARBALL = `xiaoguai-v${XIAOGUAI_VERSION}-x86_64-unknown-linux-gnu.tar.gz`;

// Pure: install commands carry the {{AGENT_PKG_BASE_URL}} / {{AGENT_USER}}
// placeholders, resolved server-side by cloudinit/index.ts. No env read here.
const AGENT_INSTALL: Record<AgentType, readonly string[]> = {
  // Goose CLI — sha256-verified tarball, system install. One &&-chained
  // command on purpose: cloud-init runcmd does NOT stop on a failed list
  // item, so the checksum gate only protects the install if download →
  // verify → install share a single chain. Nothing from the download is
  // installed unless sha256sum -c passes. (Tarball member is "./goose".)
  // System tool → /usr/local/bin, runs as root, no `su`.
  goose: [
    `curl -fsSL -o /tmp/goose.tar.bz2 ${AGENT_PKG_BASE_URL_TOKEN}/${GOOSE_TARBALL}` +
      ` && echo "${GOOSE_SHA256}  /tmp/goose.tar.bz2" | sha256sum -c -` +
      " && tar -xjf /tmp/goose.tar.bz2 -C /tmp ./goose" +
      " && install -m 755 /tmp/goose /usr/local/bin/goose" +
      " && rm -f /tmp/goose.tar.bz2 /tmp/goose",
  ],
  // xiaoguai (小怪) — self-contained binary into the user's home. Runs under
  // `su - {{AGENT_USER}}` (runcmd is root; the binary belongs in the deploy
  // user's ~/.local/bin, see doc 33 §4.1). One &&-chain inside `su -c` so
  // each step gates the next; the sha256sum -c gate sits between download and
  // extract so nothing from the tarball is unpacked/executed unless the bytes
  // match the operator-supplied hash. The final --version leaves a non-zero
  // trail in cloud-init logs if the install silently failed. Not pipe-to-shell.
  xiaoguai: [
    `su - ${AGENT_USER_TOKEN} -c "curl -fsSL ${AGENT_PKG_BASE_URL_TOKEN}/${XIAOGUAI_TARBALL} -o /tmp/xiaoguai.tgz` +
      ` && echo '${AGENT_XIAOGUAI_SHA256_TOKEN}  /tmp/xiaoguai.tgz' | sha256sum -c -` +
      " && mkdir -p ~/.local/bin" +
      " && tar -xzf /tmp/xiaoguai.tgz -C ~/.local/bin" +
      " && rm -f /tmp/xiaoguai.tgz" +
      ' && ~/.local/bin/xiaoguai --version"',
  ],
};

export function isKnownAgentType(value: string): value is AgentType {
  return (KNOWN_AGENT_TYPES as readonly string[]).includes(value);
}

/**
 * Resolve the runcmd command group for a known agent type. Pure — the returned
 * commands still contain {{AGENT_PKG_BASE_URL}} / {{AGENT_USER}} placeholders,
 * substituted server-side in cloudinit/index.ts. Throws a teaching error
 * listing the supported types when given an unknown one.
 */
export function getAgentRuncmd(agentType: string): readonly string[] {
  if (!isKnownAgentType(agentType)) {
    throw new Error(
      `Unknown agent type "${agentType}". Known types: ${KNOWN_AGENT_TYPES.join(", ")}. ` +
        `Leave agentType empty to supply install commands manually.`,
    );
  }
  return AGENT_INSTALL[agentType];
}
