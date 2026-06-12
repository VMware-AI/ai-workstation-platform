import { prisma } from "@/lib/prisma";
import { transitionInstance } from "@/lib/instance-transition";
import { resolveProvider } from "@/lib/providers";
import { logger } from "@/lib/logger";

// Configurable (#90 M24): large templates on slow storage can legitimately
// exceed the default; ops can stretch the window without a code change.
// MUST stay larger than the vm.clone budget (govc.ts CLONE_TIMEOUT_MS = 30 min)
// plus the IP wait (~5 min) — otherwise the reaper destroys a VM whose clone
// is still legitimately running. Default 45 min leaves ~10 min of margin.
const PROVISION_TIMEOUT_MS =
  Number(process.env.PROVISION_TIMEOUT_MS) > 0
    ? Number(process.env.PROVISION_TIMEOUT_MS)
    : 45 * 60 * 1000; // default 45 minutes

// Interpolated into operator-facing messages so they keep telling the truth
// when ops stretches PROVISION_TIMEOUT_MS past the 45-min default. Floor at 1
// so a sub-minute custom timeout never prints a misleading ">0 min".
const TIMEOUT_MINUTES = Math.max(1, Math.round(PROVISION_TIMEOUT_MS / 60000));

export async function runReaper(): Promise<void> {
  const cutoff = new Date(Date.now() - PROVISION_TIMEOUT_MS);

  // PENDING and STOPPING joined the sweep with #236: lifecycle transitions
  // are now guarded, so a PENDING row whose queue job was lost or a STOPPING
  // row whose in-process stop died with the server would otherwise 409
  // forever. ERROR re-opens the guarded retry path.
  const stale = await prisma.instance.findMany({
    where: {
      status: { in: ["PENDING", "PROVISIONING", "INITIALIZING", "STOPPING"] },
      updatedAt: { lt: cutoff },
    },
    select: {
      id: true,
      name: true,
      status: true,
      vmRefId: true,
      startedAt: true,
      computePool: { select: { type: true } },
    },
  });

  if (stale.length === 0) return;

  logger.info("reaper: stale instances found", { count: stale.length });

  for (const inst of stale) {
    // Tear down the half-provisioned VM before marking ERROR — without this,
    // every timeout leaked a powered-on orphan in vCenter (#90 M23).
    //
    // Two guards (PR #232 review HIGH-1/HIGH-2):
    // - vmRefId is only written when an instance reaches RUNNING, so a fresh
    //   provision stuck in PROVISIONING always has vmRefId=null — destroy by
    //   the instance name instead (the provider clones the VM under exactly
    //   that name).
    // - startedAt set means this instance once reached RUNNING (a restart
    //   re-enqueued provisioning). That VM holds user state — NEVER destroy
    //   it from the reaper; flag for manual verification instead.
    const provider = resolveProvider(inst.computePool?.type);
    let cleanup: string;
    if (inst.status === "PENDING" && inst.startedAt === null && inst.vmRefId === null) {
      // The provision worker never picked the job up — nothing was cloned.
      cleanup = "provision job never started — no VM to destroy";
    } else if (!provider) {
      cleanup = "no provider for pool — nothing to destroy";
    } else if (inst.startedAt !== null) {
      cleanup =
        "instance previously reached RUNNING — NOT destroying the VM; " +
        "verify its state in vCenter manually";
    } else {
      try {
        await provider.destroy(inst.id, inst.vmRefId ?? inst.name);
        cleanup = "stale VM destroyed";
      } catch (e) {
        const detail = e instanceof Error ? e.message : String(e);
        cleanup = `destroy failed: ${detail} — VM may be ORPHANED, clean up manually`;
        logger.error("reaper: destroy failed", { instanceId: inst.id, error: detail });
      }
    }

    // Guarded (#244): if the row legitimately moved on while we were
    // destroying (stop completed → STOPPED, or a DELETE landed), our ERROR
    // write must not clobber it — and the timeout event would be a lie.
    const marked = await transitionInstance(inst.id, [inst.status], "ERROR", {
      errorMessage: `Lifecycle timeout: stuck in ${inst.status} for >${TIMEOUT_MINUTES} min (${cleanup})`,
    });
    if (!marked) {
      logger.info("reaper: row moved on during sweep; skipping", { instanceId: inst.id });
      continue;
    }
    await prisma.provisionEvent.create({
      data: {
        instanceId: inst.id,
        event: "TIMEOUT",
        message: `Reaper: instance was in ${inst.status} for more than ${TIMEOUT_MINUTES} minutes; ${cleanup}`,
      },
    });
  }
}
