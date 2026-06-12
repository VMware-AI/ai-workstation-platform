import {
  AGENT_PKG_BASE_URL_TOKEN,
  AGENT_USER_TOKEN,
  AGENT_XIAOGUAI_SHA256_TOKEN,
  getAgentRuncmd,
} from "./agents";
import {
  generateMetadata,
  generateUserdata,
  type NetworkConfig,
} from "./userdata";

// Public entry point for form-driven cloud-init (doc 33). The deploy page
// (P2-d) and the govc VSphereProvider (P2-c) call buildDeployCloudInit to turn
// one row of form/CSV input into the {userdata, metadata} pair injected via
// guestinfo. Pure logic — no VC, no I/O — so it is fully unit-testable.

export {
  generateMetadata,
  generateNetworkConfig,
  generateUserdata,
  type MetadataParams,
  type NetworkConfig,
  type UserdataParams,
} from "./userdata";
export {
  KNOWN_AGENT_TYPES,
  getAgentRuncmd,
  isKnownAgentType,
  type AgentType,
} from "./agents";
export { sha512Crypt, type Sha512CryptOptions } from "./sha512-crypt";
export { encodeRuncmd, isYamlSafe, yamlField, YamlFieldError } from "./yaml-safe";
export {
  CSV_COLUMNS,
  netmaskToPrefix,
  parseDeployCsv,
  type CsvDeployRow,
  type CsvParseResult,
  type CsvRowError,
} from "./csv";

export interface DeployCloudInitParams {
  /** Stable cloud-init instance-id (use the platform Instance id). */
  instanceId: string;
  hostname: string;
  network: NetworkConfig;
  user: string;
  password: string;
  timezone?: string;
  sshKey?: string;
  packages?: string[];
  dataDisks?: number;
  /** Known agent type → its registry install commands (doc 33 §4). */
  agentType?: string;
  /** Free-form install commands; appended after any agentType group. */
  installCommands?: string[];
  /** Fixed salt for deterministic output (tests); random otherwise. */
  passwordSalt?: string;
  /** One-shot agent bootstrap (#231): when set, a register callback is
   *  appended to runcmd AFTER the agent install commands, so the VM
   *  self-reports READY (and its IP) once cloud-init finished. */
  bootstrap?: { token: string; controlPlaneUrl: string };
}

export interface CloudInitDocuments {
  userdata: string;
  metadata: string;
}

/**
 * Build the userdata + metadata pair for one VM from deploy-form input.
 * The agent-install runcmd tail is assembled from the chosen agentType's
 * registry group plus any free-form installCommands (both honored, registry
 * first), matching the deploy page's "shared runcmd" textarea (doc 33 §6.5).
 */
export function buildDeployCloudInit(p: DeployCloudInitParams): CloudInitDocuments {
  const runcmd = resolveAgentRuncmd(p.user, p.agentType, p.installCommands);
  if (p.bootstrap) {
    runcmd.push(buildRegisterCallback(p.instanceId, p.bootstrap));
  }

  const userdata = generateUserdata({
    hostname: p.hostname,
    user: p.user,
    password: p.password,
    timezone: p.timezone,
    sshKey: p.sshKey,
    packages: p.packages,
    dataDisks: p.dataDisks,
    runcmd,
    passwordSalt: p.passwordSalt,
  });

  const metadata = generateMetadata({
    instanceId: p.instanceId,
    hostname: p.hostname,
    network: p.network,
  });

  return { userdata, metadata };
}

// Self-registration callback (#231). Runs last in runcmd so READY means the
// agent install above actually completed. Non-fatal on failure (|| echo):
// the worker's own vm.ip-wait path still marks the row RUNNING, and the
// one-shot token simply expires unused. The token is scoped to this single
// instance, sha256-verified server-side, and consumed on first register.
function buildRegisterCallback(
  instanceId: string,
  b: { token: string; controlPlaneUrl: string }
): string {
  // Shell-injection guard (PR #253 review CRITICAL-1): the URL is embedded
  // in a command that runs inside the VM. Operator-set, but a quote or $( )
  // in a mistyped value must not execute there. http(s) URLs have no need
  // for shell metacharacters — reject instead of trying to escape.
  if (!/^https?:\/\/[A-Za-z0-9.\-_:\[\]]+(?:\/[A-Za-z0-9.\-_~%\/]*)?$/.test(b.controlPlaneUrl)) {
    throw new Error(
      `CONTROL_PLANE_URL ${JSON.stringify(b.controlPlaneUrl)} is not a plain http(s) URL — ` +
        "shell metacharacters are not allowed in the register callback."
    );
  }
  const url = `${b.controlPlaneUrl.replace(/\/+$/, "")}/api/nodes/register`;
  // Inner JSON quotes are backslash-escaped so they survive the shell's
  // outer -d "..." quoting; $(hostname -I) still expands at runtime.
  const payload =
    `{\\"instanceId\\":\\"${instanceId}\\",\\"status\\":\\"READY\\",` +
    `\\"ipAddress\\":\\"$(hostname -I | awk '{print $1}')\\"}`;
  return (
    `curl -fsS -m 30 --retry 3 --retry-delay 5 -X POST` +
    ` -H "Authorization: Bearer ${b.token}"` +
    ` -H "Content-Type: application/json"` +
    ` -d "${payload}" '${url}'` +
    ` || echo "agent register callback failed (non-fatal)"`
  );
}

// Resolve the agent-install runcmd tail (doc 33 §4.1). The registry + free-form
// commands carry {{AGENT_USER}} / {{AGENT_PKG_BASE_URL}} placeholders; both are
// substituted here, SERVER-SIDE, so the browser deploy form can expand the pure
// registry without needing env (the env var is server-only) and operator-typed
// free-form commands get the same treatment.
function resolveAgentRuncmd(
  user: string,
  agentType?: string,
  installCommands?: string[],
): string[] {
  const fromRegistry = agentType ? [...getAgentRuncmd(agentType)] : [];
  const fromForm = installCommands ?? [];
  const combined = [...fromRegistry, ...fromForm];
  return substituteXiaoguaiSha256(
    substitutePkgBaseUrl(substituteAgentUser(combined, user)),
  );
}

// Replace every {{AGENT_USER}} occurrence with the deploy user (doc 33 §4.1).
// Home-dir installs (e.g. xiaoguai) reference it to `su` to the right user.
// The token is embedded in a command that runs inside the VM, so the user must
// be a valid Linux username before it is spliced in — reject anything else
// rather than try to escape it (mirrors the register-callback URL guard).
function substituteAgentUser(commands: string[], user: string): string[] {
  if (!commands.some((c) => c.includes(AGENT_USER_TOKEN))) {
    return commands;
  }
  // Matches the conventional useradd NAME_REGEX (lowercase, leading [a-z_]) —
  // intentionally stricter than POSIX (no uppercase), which fits cloud-image
  // users (ubuntu, cloud-user, …) and keeps the value safe to splice into `su`.
  if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(user)) {
    throw new Error(
      `osUser ${JSON.stringify(user)} is not a valid Linux username — ` +
        `${AGENT_USER_TOKEN} substitution requires [a-z_][a-z0-9_-]{0,31}.`,
    );
  }
  return commands.map((c) => c.split(AGENT_USER_TOKEN).join(user));
}

// Replace every {{AGENT_PKG_BASE_URL}} with the internal mirror root from
// AGENT_PKG_BASE_URL (#286). Read here (server-only) rather than in the pure
// agents.ts registry. Fails fast with a teaching error when a known agent needs
// the mirror but it is unset; rejects shell metacharacters because the value is
// spliced into a command that runs inside the VM (mirrors the URL guard above).
function substitutePkgBaseUrl(commands: string[]): string[] {
  if (!commands.some((c) => c.includes(AGENT_PKG_BASE_URL_TOKEN))) {
    return commands;
  }
  const raw = process.env.AGENT_PKG_BASE_URL?.trim();
  if (!raw) {
    throw new Error(
      "AGENT_PKG_BASE_URL is not set — it is required to install an agent offline " +
        "from the internal package mirror. Set it to the mirror root, " +
        "e.g. http://mirror.internal/agents.",
    );
  }
  const base = raw.replace(/\/+$/, "");
  if (!/^(?:https?|ftp):\/\/[A-Za-z0-9.\-_:[\]]+(?:\/[A-Za-z0-9.\-_~%/]*)?$/.test(base)) {
    throw new Error(
      `AGENT_PKG_BASE_URL ${JSON.stringify(raw)} is not a plain http(s)/ftp URL — ` +
        "it is embedded in a VM install command; shell metacharacters are not allowed.",
    );
  }
  return commands.map((c) => c.split(AGENT_PKG_BASE_URL_TOKEN).join(base));
}

// Replace every {{XIAOGUAI_SHA256}} with the operator-supplied artifact hash
// from AGENT_XIAOGUAI_SHA256 (SEC-3). Read here (server-only), like the mirror
// root above. Fail-closed: a xiaoguai install needs the gate, so an unset or
// malformed hash throws rather than letting the install proceed unverified.
// A bare 64-hex string has no shell metacharacters, but validate the shape
// anyway since it is spliced into a `sha256sum -c` line inside the VM command.
function substituteXiaoguaiSha256(commands: string[]): string[] {
  if (!commands.some((c) => c.includes(AGENT_XIAOGUAI_SHA256_TOKEN))) {
    return commands;
  }
  const raw = process.env.AGENT_XIAOGUAI_SHA256?.trim();
  if (!raw) {
    throw new Error(
      "AGENT_XIAOGUAI_SHA256 is not set — it is the sha256 of the mirrored xiaoguai " +
        "artifact, required to verify the download before install. Compute it with " +
        "`sha256sum xiaoguai-*.tar.gz` against the asset you mirrored.",
    );
  }
  if (!/^[0-9a-f]{64}$/.test(raw)) {
    throw new Error(
      `AGENT_XIAOGUAI_SHA256 ${JSON.stringify(raw)} is not a 64-char lowercase hex ` +
        "sha256 digest.",
    );
  }
  return commands.map((c) => c.split(AGENT_XIAOGUAI_SHA256_TOKEN).join(raw));
}
