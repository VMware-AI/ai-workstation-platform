import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { getAgentRuncmd, isKnownAgentType } from "./agents";

// TEMPORARY debug scaffold (#277) — NOT a product feature. The generated
// userdata/metadata are otherwise never persisted (gzip+base64'd inline into
// guestinfo at inject time, govc.ts), so there's no way to see what the UI input
// produced. This exists only to debug a stuck deploy (e.g. runcmd network
// failures while the govc path is still being proven out). Once provisioning is
// verified end-to-end, this whole file + its two call sites in ../index.ts can
// be deleted. Keep it off by default (opt-in env flag) until then.

/**
 * Secret-free one-line summary for the worker log. The agent runcmd comes from
 * the published install registry (pip/curl — no secrets); the token-bearing
 * register callback that cloudinit/index.ts appends to the real runcmd is
 * deliberately NOT included here.
 */
export function cloudInitLogSummary(
  docs: { userdata: string; metadata: string },
  agentType: string | undefined,
): Record<string, unknown> {
  const known = !!agentType && isKnownAgentType(agentType);
  return {
    userdataBytes: Buffer.byteLength(docs.userdata, "utf8"),
    metadataBytes: Buffer.byteLength(docs.metadata, "utf8"),
    agentType: agentType || "none",
    // Truncated so a long install chain doesn't flood the log line. Commands
    // still carry the {{AGENT_PKG_BASE_URL}} / {{AGENT_USER}} placeholders here
    // (resolved later, server-side) — fine for a log preview.
    agentInstall: known ? getAgentRuncmd(agentType as string).join(" ; ").slice(0, 300) : undefined,
  };
}

/**
 * Opt-in: when CLOUDINIT_DEBUG_DIR is set, write the RAW userdata/metadata to
 * disk so the exact generated docs can be inspected without extracting them
 * back out of the VM. Returns the directory it wrote to, or null if disabled.
 *
 * SECURITY: the userdata contains secrets (bootstrap token, any embedded key),
 * so files are written 0600 and this must stay OFF in production — it exists to
 * debug a deploy, delete the files after. Caller wraps this so a debug-only
 * failure never breaks a real provision.
 */
export function persistCloudInitDebug(
  vmName: string,
  docs: { userdata: string; metadata: string },
  dir: string | undefined = process.env.CLOUDINIT_DEBUG_DIR,
): string | null {
  if (!dir) return null;
  const safe = vmName.replace(/[^a-zA-Z0-9_.-]/g, "_") || "vm";
  // 0700 so the filenames (= vm names) aren't world-listable either.
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  writeFileSync(join(dir, `${safe}.userdata.yaml`), docs.userdata, { mode: 0o600 });
  writeFileSync(join(dir, `${safe}.metadata.yaml`), docs.metadata, { mode: 0o600 });
  return dir;
}
