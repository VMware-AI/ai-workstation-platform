import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { Prisma } from "@prisma/client";
import { withTenantRole } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { enqueueProvision, countProvisionWorkers } from "@/lib/queue";
import { encrypt } from "@/lib/crypto";
import {
  buildInstancesFromDeploy,
  SECRET_DEPLOY_FIELDS,
  type DeployParams,
  type DeployRequest,
} from "@/lib/providers/vsphere/cloudinit/deploy-params";

// Native vSphere deploy (doc 33 §6.5 page 3): create one or many Instances from
// the deploy form (guided single VM or CSV batch). Cloud-init is generated later
// at clone time by the govc provider (P2-c); here we validate + persist
// deployParams with secrets encrypted, then enqueue provisioning per VM.

const networkSchema = z.discriminatedUnion("mode", [
  z.object({ mode: z.literal("dhcp") }),
  z.object({
    mode: z.literal("static"),
    ip: z.string(),
    prefix: z.number().int(),
    gateway: z.string(),
    dns: z.array(z.string()),
  }),
]);

const sharedSchema = z.object({
  templateName: z.string(),
  datastore: z.string().optional(),
  network: z.string().optional(),
  resourcePool: z.string().optional(),
  folder: z.string().optional(),
  timezone: z.string().optional(),
  packages: z.array(z.string()).optional(),
  dataDisks: z.number().int().optional(),
  agentType: z.string().optional(),
  installCommands: z.array(z.string()).optional(),
  llmBaseUrl: z.string().optional(),
  llmApiKey: z.string().optional(),
});

const bodySchema = z
  .object({
    computePoolId: z.string().min(1),
    mode: z.enum(["single", "batch"]),
    shared: sharedSchema,
    vm: z
      .object({
        name: z.string().min(1).max(100),
        network: networkSchema,
        osUser: z.string(),
        osPassword: z.string(),
        sshKey: z.string().optional(),
      })
      .optional(),
    csv: z.string().optional(),
  })
  .refine((b) => (b.mode === "single" ? !!b.vm : !!b.csv), {
    message: "single mode requires `vm`; batch mode requires `csv`",
  });

// Encrypt the secret fields (osPassword, llmApiKey) so they never sit in the DB
// in plaintext. llmApiKey is later delivered to the agent via /api/nodes/credentials.
function encryptSecrets(params: DeployParams): DeployParams {
  const out = { ...params };
  for (const field of SECRET_DEPLOY_FIELDS) {
    const value = out[field];
    if (value) out[field] = encrypt(value);
  }
  return out;
}

export async function POST(req: NextRequest) {
  return withTenantRole(req, async (session) => {
    // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
    const parsed = bodySchema.safeParse(await req.json().catch(() => null));
    if (!parsed.success) {
      return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
    }
    const body = parsed.data;

    const pool = await prisma.computePool.findFirst({
      where: { id: body.computePoolId, tenantId: session.tenantId },
    });
    if (!pool) return NextResponse.json({ error: "计算池不存在" }, { status: 404 });
    if (pool.type !== "vsphere") {
      return NextResponse.json({ error: "部署仅支持 vSphere 计算池。" }, { status: 400 });
    }

    const request: DeployRequest =
      body.mode === "single"
        ? { mode: "single", shared: body.shared, vm: body.vm! }
        : { mode: "batch", shared: body.shared, csv: body.csv! };

    const built = buildInstancesFromDeploy(request);
    if (built.errors.length > 0) {
      return NextResponse.json({ error: "部署参数校验失败", details: built.errors }, { status: 400 });
    }

    // Quota + create must be atomic for the whole batch: lock the tenant row,
    // check that running + N stays within quota, then create all N at once.
    const count = built.instances.length;
    const result = await prisma.$transaction(async (tx) => {
      await tx.$queryRaw`SELECT id FROM "Tenant" WHERE id = ${session.tenantId} FOR UPDATE`;
      const tenant = await tx.tenant.findUnique({ where: { id: session.tenantId } });
      // Count every live state (same set as the compute-pool delete check):
      // PENDING/RUNNING alone let a burst of deploys slip past quota while
      // earlier batches sat in PROVISIONING/INITIALIZING.
      const running = await tx.instance.count({
        where: {
          tenantId: session.tenantId,
          status: { in: ["PENDING", "PROVISIONING", "INITIALIZING", "RUNNING"] },
        },
      });
      if (tenant && running + count > tenant.quotaInstances) {
        return { quotaExceeded: tenant.quotaInstances, running } as const;
      }
      const created = [];
      for (const inst of built.instances) {
        created.push(
          await tx.instance.create({
            data: {
              tenantId: session.tenantId,
              name: inst.name,
              status: "PENDING",
              computePoolId: body.computePoolId,
              deployParams: encryptSecrets(inst.deployParams) as unknown as Prisma.InputJsonValue,
            },
          }),
        );
      }
      return { created } as const;
    });

    if ("quotaExceeded" in result) {
      return NextResponse.json(
        {
          error: `批量 ${count} 台将超出实例配额上限 (${result.quotaExceeded}，当前 ${result.running})`,
        },
        { status: 429 },
      );
    }

    const enqueueFailed: string[] = [];
    let queueDown = false;
    for (const instance of result.created) {
      if (!queueDown) {
        try {
          await enqueueProvision({
            instanceId: instance.id,
            tenantId: session.tenantId,
            computePoolId: body.computePoolId,
          });
          continue;
        } catch {
          // Redis unreachable: it's a global outage, so stop retrying and mark
          // the rest ERROR immediately instead of waiting out a timeout each.
          queueDown = true;
        }
      }
      // Don't leave a silent PENDING orphan no worker will ever pick up.
      enqueueFailed.push(instance.name);
      await prisma.instance.update({
        where: { id: instance.id, tenantId: session.tenantId },
        data: {
          status: "ERROR",
          errorMessage:
            "制备任务入队失败：队列服务（Redis）不可达。请确认 redis 在运行、worker 已启动后重新部署。",
        },
      });
    }

    if (enqueueFailed.length > 0) {
      return NextResponse.json(
        {
          error: `制备入队失败 ${enqueueFailed.length}/${count} 台（队列服务 Redis 不可达），已标记为 ERROR：${enqueueFailed.join(", ")}。请检查 redis 与 worker 后重新部署。`,
          created: result.created.map((i) => ({ id: i.id, name: i.name })),
        },
        { status: 503 },
      );
    }

    // All enqueued. Warn (but still 201) if no worker is online to consume the
    // jobs — otherwise the instances sit at PENDING with no UI signal (#259).
    // Only warn on a definitive zero; null = couldn't check → stay quiet.
    const created = result.created.map((i) => ({ id: i.id, name: i.name }));
    const workerCount = await countProvisionWorkers();
    const warning =
      workerCount === 0
        ? "已入队，但当前没有制备 worker 在线 —— 实例会停在 PENDING 直到 worker 启动。请启动 worker：docker compose up -d provisioner-worker（或 npm run worker）。"
        : undefined;

    return NextResponse.json(
      warning ? { created, warning } : { created },
      { status: 201 },
    );
  });
}
