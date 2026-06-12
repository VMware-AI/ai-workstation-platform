import { isKnownAgentType } from "./agents";
import { parseDeployCsv } from "./csv";
import { isYamlSafe } from "./yaml-safe";
import type { NetworkConfig } from "./userdata";

// Assemble + validate a deploy-page request (doc 33 §6.5 page 3) into per-VM
// params stored on each Instance. Pure logic — no encryption, no DB — so it is
// unit-testable; the API layer encrypts osPassword/llmApiKey before persisting
// and the govc provider (P2-c) turns deployParams into cloud-init at clone time.

export const MAX_BATCH = 100;

/** Shared inputs applied to every VM in a deploy (single or batch). */
export interface SharedDeploy {
  /** VC VM template to clone (from the pool inventory). Required. */
  templateName: string;
  // Placement (clone-time), chosen from the pool's live inventory.
  datastore?: string;
  network?: string;
  resourcePool?: string;
  folder?: string;
  // cloud-init extras shared across the batch.
  timezone?: string;
  packages?: string[];
  dataDisks?: number;
  /** Known agent type whose install commands run in runcmd. */
  agentType?: string;
  /** Free-form shared runcmd (agent install), appended after agentType group. */
  installCommands?: string[];
  /** LLM access handed to the in-VM agent (§5.5). */
  llmBaseUrl?: string;
  llmApiKey?: string;
}

/** One guided single VM. name doubles as hostname. */
export interface SingleVm {
  name: string;
  network: NetworkConfig;
  osUser: string;
  osPassword: string;
  sshKey?: string;
}

export type DeployRequest =
  | { mode: "single"; shared: SharedDeploy; vm: SingleVm }
  | { mode: "batch"; shared: SharedDeploy; csv: string };

/** Per-Instance config persisted as Instance.deployParams (secrets plaintext here). */
export interface DeployParams {
  templateName: string;
  datastore?: string;
  network?: string;
  resourcePool?: string;
  folder?: string;
  hostname: string;
  osUser: string;
  osPassword: string;
  sshKey?: string;
  timezone?: string;
  netConfig: NetworkConfig;
  packages?: string[];
  dataDisks?: number;
  agentType?: string;
  installCommands?: string[];
  llmBaseUrl?: string;
  llmApiKey?: string;
}

export interface BuiltInstance {
  name: string;
  deployParams: DeployParams;
}

export interface DeployBuildResult {
  instances: BuiltInstance[];
  errors: { line?: number; message: string }[];
}

/** Field names whose values are secrets and must be encrypted before storage. */
export const SECRET_DEPLOY_FIELDS = ["osPassword", "llmApiKey"] as const;

/**
 * Validate a deploy request and expand it into per-VM instances. Collects all
 * errors (per CSV row where applicable) instead of throwing, so the UI/API can
 * surface every problem at once. On any error, `instances` is empty.
 */
export function buildInstancesFromDeploy(req: DeployRequest): DeployBuildResult {
  const errors: { line?: number; message: string }[] = [];

  const sharedErrors = validateShared(req.shared);
  errors.push(...sharedErrors);

  let rows: { name: string; params: DeployParams }[] = [];

  if (req.mode === "single") {
    const vmErrors = validateSingleVm(req.vm);
    errors.push(...vmErrors);
    if (vmErrors.length === 0 && sharedErrors.length === 0) {
      rows = [{ name: req.vm.name, params: singleToParams(req.shared, req.vm) }];
    }
  } else {
    const parsed = parseDeployCsv(req.csv);
    errors.push(...parsed.errors);
    if (parsed.rows.length === 0 && parsed.errors.length === 0) {
      errors.push({ message: "CSV has no VM rows" });
    }
    if (parsed.rows.length > MAX_BATCH) {
      errors.push({ message: `Batch too large: ${parsed.rows.length} rows (max ${MAX_BATCH})` });
    }
    if (errors.length === 0) {
      rows = parsed.rows.map((r) => ({
        name: r.vmName,
        params: csvRowToParams(req.shared, r),
      }));
    }
  }

  if (errors.length > 0) return { instances: [], errors };
  return { instances: rows.map((r) => ({ name: r.name, deployParams: r.params })), errors: [] };
}

function validateShared(s: SharedDeploy): { message: string }[] {
  const errors: { message: string }[] = [];
  if (!s.templateName) {
    errors.push({ message: "templateName (VC template) is required" });
  }
  for (const [name, val] of [
    ["templateName", s.templateName],
    ["datastore", s.datastore],
    ["network", s.network],
    ["resourcePool", s.resourcePool],
    ["folder", s.folder],
    ["timezone", s.timezone],
  ] as const) {
    if (val && !isYamlSafe(val)) errors.push({ message: `${name} contains unsafe characters` });
  }
  if (s.dataDisks !== undefined && (!Number.isInteger(s.dataDisks) || s.dataDisks < 0 || s.dataDisks > 9)) {
    errors.push({ message: `dataDisks must be an integer 0-9` });
  }
  if (s.agentType && !isKnownAgentType(s.agentType)) {
    errors.push({ message: `unknown agentType "${s.agentType}"` });
  }
  if (s.packages) {
    for (const p of s.packages) {
      if (!isYamlSafe(p)) errors.push({ message: `package "${p}" contains unsafe characters` });
    }
  }
  return errors;
}

function validateSingleVm(vm: SingleVm): { message: string }[] {
  const errors: { message: string }[] = [];
  if (!vm.name) errors.push({ message: "VM name is required" });
  if (!vm.osUser) errors.push({ message: "osUser is required" });
  if (!vm.osPassword) errors.push({ message: "osPassword is required" });
  for (const [name, val] of [
    ["name", vm.name],
    ["osUser", vm.osUser],
    ["sshKey", vm.sshKey],
  ] as const) {
    if (val && !isYamlSafe(val)) errors.push({ message: `${name} contains unsafe characters` });
  }
  errors.push(...validateNetwork(vm.network));
  return errors;
}

function validateNetwork(net: NetworkConfig): { message: string }[] {
  if (net.mode === "dhcp") return [];
  const errors: { message: string }[] = [];
  if (!net.ip || !isYamlSafe(net.ip)) errors.push({ message: "invalid static ip" });
  if (!net.gateway || !isYamlSafe(net.gateway)) errors.push({ message: "invalid gateway" });
  if (!Number.isInteger(net.prefix) || net.prefix < 0 || net.prefix > 32) {
    errors.push({ message: "invalid network prefix" });
  }
  if (net.dns.length === 0 || net.dns.some((d) => !isYamlSafe(d))) {
    errors.push({ message: "invalid dns" });
  }
  return errors;
}

function sharedToParams(s: SharedDeploy): Omit<DeployParams, "hostname" | "osUser" | "osPassword" | "sshKey" | "netConfig"> {
  return {
    templateName: s.templateName,
    ...(s.datastore ? { datastore: s.datastore } : {}),
    ...(s.network ? { network: s.network } : {}),
    ...(s.resourcePool ? { resourcePool: s.resourcePool } : {}),
    ...(s.folder ? { folder: s.folder } : {}),
    ...(s.timezone ? { timezone: s.timezone } : {}),
    ...(s.packages && s.packages.length ? { packages: s.packages } : {}),
    ...(s.dataDisks ? { dataDisks: s.dataDisks } : {}),
    ...(s.agentType ? { agentType: s.agentType } : {}),
    ...(s.installCommands && s.installCommands.length ? { installCommands: s.installCommands } : {}),
    ...(s.llmBaseUrl ? { llmBaseUrl: s.llmBaseUrl } : {}),
    ...(s.llmApiKey ? { llmApiKey: s.llmApiKey } : {}),
  };
}

function singleToParams(s: SharedDeploy, vm: SingleVm): DeployParams {
  return {
    ...sharedToParams(s),
    hostname: vm.name,
    osUser: vm.osUser,
    osPassword: vm.osPassword,
    ...(vm.sshKey ? { sshKey: vm.sshKey } : {}),
    netConfig: vm.network,
  };
}

function csvRowToParams(
  s: SharedDeploy,
  r: { hostname: string; user: string; password: string; sshKey?: string; network: NetworkConfig },
): DeployParams {
  return {
    ...sharedToParams(s),
    hostname: r.hostname,
    osUser: r.user,
    osPassword: r.password,
    ...(r.sshKey ? { sshKey: r.sshKey } : {}),
    netConfig: r.network,
  };
}
