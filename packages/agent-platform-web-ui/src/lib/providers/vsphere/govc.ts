import { spawn } from "node:child_process";
import { gzipSync } from "node:zlib";
import { teachVcError } from "./teach-vc-error";

// Thin wrapper around the `govc` CLI (govmomi). Inventory listing and the
// eventual clone/guestinfo-inject path both go through this — govc reliably
// enumerates VM templates (.vmtx), which the vCenter REST API does not. Mirrors
// the proven command set in yxl1983123/vm-deploy's lib/govc.sh.

export interface VcConn {
  host: string;
  username: string;
  password: string;
  datacenter?: string;
  // Verify the vCenter TLS cert. Default true; false only for self-signed labs.
  verifySsl?: boolean;
}

export class GovcError extends Error {}

export interface VcInventory {
  datastores: string[];
  networks: string[];
  resourcePools: string[];
  folders: string[];
  templates: string[];
}

// Map raw govc stderr to an actionable message. Delegates to the shared
// teachVcError so the test/inventory routes never echo raw stderr to the
// browser (#357 item 7); the generic fallback replaces the old
// `govc 失败：${stderr}` leak. Raw stderr still reaches server logs via the
// caller that constructs the spawn error.
function teachGovc(stderr: string): string {
  return teachVcError(stderr, {
    certHint: "TLS 证书校验失败：自签名证书环境请在计算池关闭证书校验。",
  });
}

function runGovc(args: string[], conn: VcConn, timeoutMs = 30000): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("govc", args, {
      env: {
        ...process.env,
        GOVC_URL: `https://${conn.host}/sdk`,
        GOVC_USERNAME: conn.username,
        GOVC_PASSWORD: conn.password,
        GOVC_INSECURE: conn.verifySsl === false ? "true" : "false",
        ...(conn.datacenter ? { GOVC_DATACENTER: conn.datacenter } : {}),
      },
    });

    let out = "";
    let err = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new GovcError("govc 命令超时（检查 VC 网络可达性）。"));
    }, timeoutMs);

    child.on("error", (e: NodeJS.ErrnoException) => {
      clearTimeout(timer);
      if (e.code === "ENOENT") {
        reject(new GovcError("govc 未安装：请在运行平台的服务器上安装 govc 二进制（https://github.com/vmware/govmomi/releases）。"));
      } else {
        reject(new GovcError(`govc 无法执行：${e.message}`));
      }
    });
    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (err += d.toString()));
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new GovcError(teachGovc(err || `govc exited ${code}`)));
    });
  });
}

// `govc find . -type <t>` returns inventory paths (one per line). Paths (not
// bare names) are what clone/placement need downstream, so we keep them whole.
async function find(conn: VcConn, args: string[]): Promise<string[]> {
  const out = await runGovc(["find", ".", ...args], conn);
  return out
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
    .sort();
}

export async function testConnection(conn: VcConn): Promise<void> {
  // `govc about` succeeds only with a valid login + reachable endpoint.
  await runGovc(["about"], conn, 15000);
}

// `govc find -type f` returns EVERY folder type (vm / host / network /
// datastore). A VM can only be cloned into a VM folder — under a datacenter's
// `vm` subtree (e.g. /home/vm, /home/vm/vCLS). Keeping the others makes the
// deploy dropdown offer invalid targets that fail vm.clone with
// "A specified parameter was not correct: spec.folder". Filter to VM folders.
export function filterVmFolders(folders: string[]): string[] {
  return folders.filter((p) => p.split("/").includes("vm"));
}

export async function listInventory(conn: VcConn): Promise<VcInventory> {
  const [datastores, networks, resourcePools, allFolders, templates] = await Promise.all([
    find(conn, ["-type", "s"]), // Datastore
    find(conn, ["-type", "n"]), // Network / portgroup
    find(conn, ["-type", "p"]), // ResourcePool
    find(conn, ["-type", "f"]), // Folder (all types — filtered to VM folders below)
    find(conn, ["-type", "m", "-config.template", "true"]), // VM templates
  ]);
  return { datastores, networks, resourcePools, folders: filterVmFolders(allFolders), templates };
}

// ── Clone + guestinfo inject (P2-c) ────────────────────────────────────────
// cloud-init's GuestInfo datasource reads guestinfo.userdata/metadata when they
// are gzip+base64 encoded (encoding=gzip+base64). The clone/inject/power/wait
// sequence mirrors vm-deploy's lib/govc.sh. The govc subprocess can't run
// without a reachable vCenter, so these ops take an injectable GovcRunner to
// stay unit-testable (orchestration + arg construction verified with a fake).

export type GovcRunner = (args: string[], timeoutMs?: number) => Promise<string>;

/** Bind a runner to a connection's env. Production callers use this. */
export function makeRunner(conn: VcConn): GovcRunner {
  return (args, timeoutMs) => runGovc(args, conn, timeoutMs);
}

/** gzip then base64 — the encoding cloud-init expects for guestinfo.* values. */
export function encodeGuestinfo(text: string): string {
  return gzipSync(Buffer.from(text, "utf8")).toString("base64");
}

export interface CloneSpec {
  /** VC VM template (govc path or name). */
  templateName: string;
  /** New VM name. */
  name: string;
  datastore?: string;
  network?: string;
  resourcePool?: string;
  folder?: string;
}

export const CLONE_TIMEOUT_MS = 1_800_000; // 30 min — clone of large/slow templates can take a while

/** Clone a template into a powered-off VM (so we can inject before first boot). */
export async function cloneOff(run: GovcRunner, spec: CloneSpec): Promise<void> {
  const args = ["vm.clone", "-vm", spec.templateName, "-on=false"];
  if (spec.datastore) args.push("-ds", spec.datastore);
  if (spec.resourcePool) args.push("-pool", spec.resourcePool);
  if (spec.folder) args.push("-folder", spec.folder);
  if (spec.network) args.push("-net", spec.network);
  args.push(spec.name);
  await run(args, CLONE_TIMEOUT_MS);
}

/** Inject gzip+base64 userdata/metadata into the powered-off VM's ExtraConfig. */
export async function injectGuestinfo(
  run: GovcRunner,
  vmName: string,
  docs: { userdata: string; metadata: string },
): Promise<void> {
  await run([
    "vm.change",
    "-vm",
    vmName,
    "-e",
    `guestinfo.userdata=${encodeGuestinfo(docs.userdata)}`,
    "-e",
    "guestinfo.userdata.encoding=gzip+base64",
    "-e",
    `guestinfo.metadata=${encodeGuestinfo(docs.metadata)}`,
    "-e",
    "guestinfo.metadata.encoding=gzip+base64",
  ]);
}

export async function powerOn(run: GovcRunner, vmName: string): Promise<void> {
  await run(["vm.power", "-on", vmName]);
}

/** Block until the guest reports an IP (or timeout). Returns the IP. */
export async function waitForIp(run: GovcRunner, vmName: string, timeoutSec = 300): Promise<string> {
  const out = await run(["vm.ip", "-wait", `${timeoutSec}s`, vmName], (timeoutSec + 30) * 1000);
  const ip = out.trim();
  if (!ip) throw new GovcError("VM 已开机但未获取到 IP（超时）。检查 cloud-init/网络配置。");
  return ip;
}

export async function destroyVm(run: GovcRunner, vmName: string): Promise<void> {
  await run(["vm.destroy", vmName]);
}
