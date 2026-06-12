import crypto from "crypto";
import { Job } from "bullmq";
import { ProvisionJobData } from "@/lib/queue";
import { prisma } from "@/lib/prisma";
import { transitionInstance } from "@/lib/instance-transition";
import { logger } from "@/lib/logger";
import { decrypt } from "@/lib/crypto";
import { resolveProvider } from "@/lib/providers";
import {
  SECRET_DEPLOY_FIELDS,
  type DeployParams,
} from "@/lib/providers/vsphere/cloudinit/deploy-params";

import { Prisma } from "@prisma/client";

// deployParams is persisted with osPassword/llmApiKey encrypted (deploy API).
// Decrypt them here, just before handing off to the provider.
function decryptDeployParams(raw: unknown): DeployParams {
  const dp = { ...(raw as DeployParams) };
  for (const field of SECRET_DEPLOY_FIELDS) {
    const value = dp[field];
    if (value) dp[field] = decrypt(value);
  }
  return dp;
}

// Bootstrap window: longer than the 45-min reaper timeout so a slow but
// successful provision can still register; short enough that an unused
// token dies the same hour it was born.
const BOOTSTRAP_TOKEN_TTL_MS = 60 * 60 * 1000;

async function recordEvent(instanceId: string, event: string, message: string, metadata: Prisma.InputJsonValue = {}) {
  await prisma.provisionEvent.create({ data: { instanceId, event, message, metadata } });
}

export async function processProvisionJob(job: Job<ProvisionJobData>): Promise<void> {
  const { instanceId, tenantId, computePoolId } = job.data;

  const instance = await prisma.instance.findUnique({
    where: { id: instanceId },
    include: { computePool: true },
  });

  if (!instance) throw new Error(`Instance ${instanceId} not found`);

  // Agent bootstrap token (#231): one-shot, instance-scoped, 1h TTL. Only
  // the sha256 hash lands in the DB (with the claim, atomically); the
  // plaintext rides cloud-init into the VM so the agent can call
  // /api/nodes/register (self-report READY) and /api/nodes/credentials.
  // Chain is off when CONTROL_PLANE_URL is unset — the worker's own
  // vm.ip-wait path still marks the row RUNNING.
  const controlPlaneUrl = process.env.CONTROL_PLANE_URL;
  const bootstrapToken = controlPlaneUrl ? crypto.randomBytes(32).toString("hex") : undefined;
  const tokenFields = bootstrapToken
    ? {
        agentTokenHash: crypto.createHash("sha256").update(bootstrapToken).digest("hex"),
        agentTokenExpiresAt: new Date(Date.now() + BOOTSTRAP_TOKEN_TTL_MS),
      }
    : {};

  // Guarded claim (#244): replaces the check-then-update that let a DELETE
  // landing between read and write be resurrected to PROVISIONING.
  // PROVISIONING is in the from-set so a BullMQ retry of a crashed attempt
  // can re-claim its own row.
  const claimed = await transitionInstance(instanceId, ["PENDING", "PROVISIONING"], "PROVISIONING", {
    provisionerJobId: job.id,
    ...tokenFields,
  });
  // Ownership fence for every post-claim write (PR #253 review M-2): a
  // stalled job that BullMQ requeued must not clobber the row its
  // replacement claimed — the claim stamps provisionerJobId, so scoping
  // subsequent writes to it makes stale-job interference impossible.
  const jobFence = { provisionerJobId: job.id };

  if (!claimed) {
    logger.info("provision skipped: row not claimable (deleted, errored, or already claimed)", {
      instanceId,
      statusAtRead: instance.status, // pre-claim snapshot; DB may have moved since
    });
    return;
  }
  await recordEvent(instanceId, "PROVISIONING_STARTED", "Provisioner picked up the job");

  const provider = resolveProvider(instance.computePool?.type);
  if (!provider) {
    const msg = `Instance ${instanceId} has no vSphere compute pool — only vCenter provisioning is supported. Assign the instance to a vSphere compute pool.`;
    await transitionInstance(instanceId, ["PROVISIONING"], "ERROR", { errorMessage: msg }, jobFence);
    await recordEvent(instanceId, "ERROR", msg);
    throw new Error(msg);
  }
  if (!instance.deployParams) {
    const msg = `Instance ${instanceId} has no deployParams — create it via the deploy page (doc 33 §6.5).`;
    await transitionInstance(instanceId, ["PROVISIONING"], "ERROR", { errorMessage: msg }, jobFence);
    await recordEvent(instanceId, "ERROR", msg);
    throw new Error(msg);
  }

  try {
    const result = await provider.provision({
      instanceId,
      tenantId,
      vmName: instance.name,
      deployParams: decryptDeployParams(instance.deployParams),
      computePoolId: computePoolId ?? undefined,
      ...(bootstrapToken && controlPlaneUrl
        ? { bootstrap: { token: bootstrapToken, controlPlaneUrl } }
        : {}),
    });

    // RUNNING is in the from-set (#231): the VM's register callback may
    // have flipped the row already — that is the happy path, not a clobber;
    // this write adds vmRefId/containerId which register cannot know.
    const completed = await transitionInstance(
      instanceId,
      ["PROVISIONING", "RUNNING"],
      "RUNNING",
      {
        containerId: result.containerId ?? null,
        vmRefId: result.vmRefId ?? null,
        ipAddress: result.ipAddress ?? null,
        endpoint: result.endpoint ?? null,
        startedAt: new Date(),
      },
      jobFence
    );
    if (!completed) {
      // A DELETE (or reaper) overtook us mid-provision — do not resurrect.
      // The VM the provider just created may be orphaned; say so loudly AND
      // in the audit trail (operators don't always have log access).
      logger.error("provision finished but the row moved on — VM may be orphaned, verify in vCenter", {
        instanceId,
        vmRefId: result.vmRefId,
      });
      await recordEvent(
        instanceId,
        "ORPHAN_WARNING",
        `Provision completed but the instance was deleted mid-flight — VM ${result.vmRefId ?? "(no ref)"} may be orphaned in vCenter, verify manually`
      ).catch(() => {});
      return;
    }
    await recordEvent(instanceId, "RUNNING", "Instance is ready", { endpoint: result.endpoint });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const marked = await transitionInstance(
      instanceId,
      ["PROVISIONING"],
      "ERROR",
      { errorMessage: message },
      jobFence
    );
    // No event when the write was dropped — an ERROR entry on a row a DELETE
    // already owns would be a false audit record.
    if (marked) await recordEvent(instanceId, "ERROR", message);
    throw err;
  }
}
