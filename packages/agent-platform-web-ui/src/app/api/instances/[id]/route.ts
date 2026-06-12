import { NextRequest, NextResponse } from "next/server";
import type { InstanceStatus } from "@prisma/client";
import { transitionInstance as transition } from "@/lib/instance-transition";
import { withTenant } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { resolveProvider } from "@/lib/providers";
import { enqueueProvision, countProvisionWorkers } from "@/lib/queue";
import { logger } from "@/lib/logger";
import { REQUEST_ID_HEADER } from "@/lib/request-id";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenant(req, async (session) => {
    const { id } = await params;
    const instance = await prisma.instance.findFirst({
      where: { id, tenantId: session.tenantId },
      include: {
        computePool: { select: { name: true, type: true } },
      },
    });
    if (!instance) return NextResponse.json({ error: "Not found" }, { status: 404 });
    return NextResponse.json(instance);
  });
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenant(req, async (session) => {
    const { id } = await params;
    const instance = await prisma.instance.findFirst({
      where: { id, tenantId: session.tenantId },
      include: { computePool: { select: { type: true } } },
    });
    if (!instance) return NextResponse.json({ error: "Not found" }, { status: 404 });

    const provider = resolveProvider(instance.computePool?.type);
    const vmRefId = instance.vmRefId ?? undefined;
    // Tear down infra in the background, then mark deleted. DB state is owned
    // here; the provider only touches the VM (doc 33 §5).
    const requestId = req.headers.get(REQUEST_ID_HEADER) ?? undefined;
    (async () => {
      if (provider) await provider.destroy(id, vmRefId);
      await prisma.instance.update({
        where: { id, tenantId: session.tenantId },
        data: { status: "DELETED" },
      });
    })().catch((e) =>
      logger.error("instance delete failed", { instanceId: id, error: e, requestId })
    );
    return NextResponse.json({ ok: true });
  });
}

// Response bodies are single-read streams — build a fresh one per request.
function conflict() {
  return NextResponse.json(
    { error: "Another lifecycle operation is in progress. Wait for the instance to settle, then retry." },
    { status: 409 }
  );
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenant(req, async (session) => {
    const { id } = await params;
    const url = new URL(req.url);
    const action = url.searchParams.get("action");
    const instance = await prisma.instance.findFirst({
      where: { id, tenantId: session.tenantId },
      include: { computePool: { select: { type: true } } },
    });
    if (!instance) return NextResponse.json({ error: "Not found" }, { status: 404 });
    // Lifecycle actions on a deleted row must not resurrect it (PR #235
    // review LOW-1) — the provision worker had this guard, the power-on
    // path would have bypassed it.
    if (instance.status === "DELETED") {
      return NextResponse.json({ error: "Instance is deleted" }, { status: 409 });
    }

    const provider = resolveProvider(instance.computePool?.type);
    const vmRefId = instance.vmRefId ?? undefined;

    if (action === "stop") {
      const requestId = req.headers.get(REQUEST_ID_HEADER) ?? undefined;
      // Claim the row before returning (#236). INITIALIZING and ERROR are
      // stoppable so a stuck or failed instance still has a way down.
      if (!(await transition(id, ["RUNNING", "INITIALIZING", "ERROR"], "STOPPING"))) {
        return conflict();
      }
      (async () => {
        try {
          if (provider) await provider.stop(id, vmRefId);
          await transition(id, ["STOPPING"], "STOPPED", { stoppedAt: new Date() });
        } catch (e) {
          logger.error("instance stop failed", { instanceId: id, error: e, requestId });
          // Surface the failure instead of stranding the row in STOPPING —
          // ERROR with a vmRefId is restartable from the UI.
          const msg = e instanceof Error ? e.message : String(e);
          await transition(id, ["STOPPING"], "ERROR", {
            errorMessage: `Power-off failed: ${msg.slice(0, 300)}`,
          }).catch(() => {});
        }
      })();
      return NextResponse.json({ ok: true, status: "STOPPING" });
    }
    if (action === "start" || action === "restart") {
      // An instance with a VM must be powered on, NOT re-provisioned:
      // re-running provision re-clones under the same name, the clone fails
      // (already exists), and the rollback destroys the user's original VM
      // with all its state (#233).
      if (vmRefId) {
        const requestId = req.headers.get(REQUEST_ID_HEADER) ?? undefined;
        // Claim the row before returning (#236). ERROR is startable — the
        // reaper's manual-verify marks instances ERROR while the VM itself
        // may be healthy, and the UI offers restart for exactly this case.
        const from: InstanceStatus[] =
          action === "restart" ? ["RUNNING", "STOPPED", "ERROR"] : ["STOPPED", "ERROR"];
        if (!(await transition(id, from, "INITIALIZING"))) {
          // Idempotent start (PR #235 review MEDIUM-1): powering on an
          // already-running VM makes govc exit non-zero, which would strand
          // a healthy RUNNING instance in INITIALIZING. Re-read instead of
          // trusting the pre-claim snapshot — a concurrent stop may already
          // own the row (#236), and then this must be a 409, not "ok".
          if (action === "start") {
            const fresh = await prisma.instance.findFirst({
              where: { id, tenantId: session.tenantId },
              select: { status: true },
            });
            if (fresh?.status === "RUNNING") {
              return NextResponse.json({ ok: true, status: "RUNNING" });
            }
          }
          return conflict();
        }
        (async () => {
          try {
            if (action === "restart" && provider) await provider.stop(id, vmRefId).catch(() => {});
            if (provider) await provider.start(id, vmRefId);
            // Clear any stale failure reason from a previous ERROR episode.
            await transition(id, ["INITIALIZING"], "RUNNING", { errorMessage: null });
          } catch (e) {
            logger.error("instance start failed", { instanceId: id, error: e, requestId });
            const msg = e instanceof Error ? e.message : String(e);
            await transition(id, ["INITIALIZING"], "ERROR", {
              errorMessage: `Power-on failed: ${msg.slice(0, 300)}`,
            }).catch(() => {});
          }
        })();
        return NextResponse.json({ ok: true, status: "INITIALIZING" });
      }
      // No VM was ever created (provision never reached RUNNING) —
      // provisioning is the correct path. Guarded so a double-click cannot
      // enqueue two provision jobs for the same instance (#236).
      if (!(await transition(id, ["STOPPED", "ERROR"], "PENDING"))) {
        // A RUNNING row with no VM reference is corrupt state, not a
        // concurrent operation — saying "retry later" would mislead (#249).
        if (instance.status === "RUNNING") {
          return NextResponse.json(
            { error: "Instance state inconsistent: RUNNING without a VM reference. Contact an administrator." },
            { status: 409 }
          );
        }
        return conflict();
      }
      try {
        // enqueueProvision (not provisionQueue.add): bounded by a 5s timeout so
        // a downed Redis surfaces as an error instead of hanging this request
        // forever with the row already claimed as PENDING.
        await enqueueProvision({
          instanceId: id,
          tenantId: instance.tenantId,
          computePoolId: instance.computePoolId ?? undefined,
        });
      } catch {
        // Don't leave a silent PENDING orphan no worker will ever pick up —
        // mirror the deploy route: mark ERROR with a teaching message.
        await transition(id, ["PENDING"], "ERROR", {
          errorMessage:
            "制备任务入队失败：队列服务（Redis）不可达。请确认 redis 在运行、worker 已启动后重试。",
        }).catch(() => {});
        return NextResponse.json(
          { error: "制备任务入队失败：队列服务（Redis）不可达，实例已标记为 ERROR。请检查 redis 与 worker 后重试。" },
          { status: 503 }
        );
      }
      // Enqueued. Warn (but still ok) if no worker is online to consume the
      // job — same signal as the deploy route (#259). Only warn on a
      // definitive zero; null = couldn't check → stay quiet.
      const workerCount = await countProvisionWorkers();
      const warning =
        workerCount === 0
          ? "已入队，但当前没有制备 worker 在线 —— 实例会停在 PENDING 直到 worker 启动。请启动 worker：docker compose up -d provisioner-worker（或 npm run worker）。"
          : undefined;
      return NextResponse.json(
        warning ? { ok: true, status: "PENDING", warning } : { ok: true, status: "PENDING" }
      );
    }
    return NextResponse.json({ error: "Unknown action" }, { status: 400 });
  });
}
