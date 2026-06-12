import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { withTenant, withTenantRole } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { Prisma } from "@prisma/client";
import {
  encryptPoolConfigForStorage,
  redactPoolConfigForWire,
} from "@/lib/pool-config";
import { poolSaveErrorResponse } from "@/lib/pool-errors";
import { parsePageParams } from "@/lib/pagination";
import { REQUEST_ID_HEADER } from "@/lib/request-id";

// A compute pool stores ONLY the vCenter connection (doc 33 §6.5 页1).
// Placement (template / datastore / network / resource pool / folder) is chosen
// at deploy time from the pool's live inventory, not stored on the pool.
const vsphereConfigSchema = z.object({
  host: z.string().min(1),
  username: z.string().min(1),
  password: z.string().min(1),
  datacenter: z.string().optional(),
  // false = skip TLS cert verification (self-signed lab vCenters). Omitted /
  // true = verify. Persisted into config so inventory + provisioning reuse it.
  verifySsl: z.boolean().optional(),
});

// docker pools were removed with the docker-agent runtime (doc 33 §7, #180);
// resolveProvider("docker") returns null, so accepting them here was a
// guaranteed provision-time failure (#90 M14).
const schema = z.object({
  name: z.string().min(1),
  type: z.literal("vsphere"),
  config: vsphereConfigSchema,
});

export async function GET(req: NextRequest) {
  return withTenant(req, async (session) => {
    const page = parsePageParams(new URL(req.url));
    if ("error" in page) return NextResponse.json({ error: page.error }, { status: 400 });
    const [pools, total] = await prisma.$transaction([
      prisma.computePool.findMany({
        where: { tenantId: session.tenantId },
        orderBy: { createdAt: "desc" },
        skip: page.skip,
        take: page.take, // #255: 真分页
      }),
      prisma.computePool.count({ where: { tenantId: session.tenantId } }),
    ]);
    return NextResponse.json({
      items: pools.map((p: typeof pools[number]) => ({
        ...p,
        config: redactPoolConfigForWire((p.config ?? {}) as Record<string, unknown>),
      })),
      total,
      skip: page.skip,
      take: page.take,
    });
  });
}

export async function POST(req: NextRequest) {
  return withTenantRole(req, async (session) => {
    // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
    const body = await req.json().catch(() => null);
    // Teaching error for the removed pool type (#90 M14) — the zod literal
    // failure alone would be an opaque "invalid_literal".
    if ((body as { type?: unknown })?.type === "docker") {
      return NextResponse.json(
        { error: "docker pools are no longer supported (doc 33 §7) — create a vsphere pool instead" },
        { status: 400 }
      );
    }
    const parsed = schema.safeParse(body);
    if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

    try {
      const storedConfig = encryptPoolConfigForStorage(
        (parsed.data.config ?? {}) as Record<string, unknown>
      );

      const pool = await prisma.computePool.create({
        data: {
          tenantId: session.tenantId,
          name: parsed.data.name,
          type: parsed.data.type,
          config: storedConfig as Prisma.InputJsonValue,
        },
      });
      return NextResponse.json(
        {
          ...pool,
          config: redactPoolConfigForWire((pool.config ?? {}) as Record<string, unknown>),
        },
        { status: 201 }
      );
    } catch (e) {
      return poolSaveErrorResponse(e, req.headers.get(REQUEST_ID_HEADER) ?? undefined);
    }
  });
}
