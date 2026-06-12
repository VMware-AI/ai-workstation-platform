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
import { REQUEST_ID_HEADER } from "@/lib/request-id";

// Same connection shape as POST (route.ts), but every field optional for a
// partial update. `.strict()` rejects unknown keys so a PATCH can't smuggle in
// arbitrary config the create path would have refused (#357 item 5). password
// stays optional: an absent password means "keep the current one", and the
// redaction sentinel "****" is handled downstream by encryptPoolConfigForStorage.
const vsphereConfigSchema = z
  .object({
    host: z.string().min(1),
    username: z.string().min(1),
    password: z.string().min(1),
    datacenter: z.string(),
    verifySsl: z.boolean(),
  })
  .partial()
  .strict();

const patchSchema = z.object({
  name: z.string().min(1).optional(),
  config: vsphereConfigSchema.optional(),
  enabled: z.boolean().optional(),
});

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenant(req, async (session) => {
    const { id } = await params;
    const pool = await prisma.computePool.findFirst({ where: { id, tenantId: session.tenantId } });
    if (!pool) return NextResponse.json({ error: "Not found" }, { status: 404 });
    return NextResponse.json({
      ...pool,
      config: redactPoolConfigForWire((pool.config ?? {}) as Record<string, unknown>),
    });
  });
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenantRole(req, async (session) => {
    const { id } = await params;
    const pool = await prisma.computePool.findFirst({ where: { id, tenantId: session.tenantId } });
    if (!pool) return NextResponse.json({ error: "Not found" }, { status: 404 });

    // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
    const body = await req.json().catch(() => null);
    const parsed = patchSchema.safeParse(body);
    if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

    try {
      let nextConfig: Prisma.InputJsonValue | undefined;
      if (parsed.data.config !== undefined) {
        const existing = (pool.config ?? {}) as Record<string, unknown>;
        let previousEncrypted =
          typeof existing.passwordEncrypted === "string" ? existing.passwordEncrypted : undefined;
        if (!previousEncrypted && typeof existing.password === "string" && existing.password.length > 0) {
          const migrated = encryptPoolConfigForStorage({ password: existing.password });
          previousEncrypted =
            typeof migrated.passwordEncrypted === "string" ? migrated.passwordEncrypted : undefined;
        }
        nextConfig = encryptPoolConfigForStorage(
          parsed.data.config as Record<string, unknown>,
          previousEncrypted
        ) as Prisma.InputJsonValue;
      }

      const updated = await prisma.computePool.update({
        // Atomic scope (#90 M6): the findFirst above checked ownership, but
        // the write itself must carry the tenant filter too.
        where: { id, tenantId: session.tenantId },
        data: {
          ...(parsed.data.name !== undefined ? { name: parsed.data.name } : {}),
          ...(nextConfig !== undefined ? { config: nextConfig } : {}),
          ...(parsed.data.enabled !== undefined ? { enabled: parsed.data.enabled } : {}),
        },
      });
      return NextResponse.json({
        ...updated,
        config: redactPoolConfigForWire((updated.config ?? {}) as Record<string, unknown>),
      });
    } catch (e) {
      return poolSaveErrorResponse(e, req.headers.get(REQUEST_ID_HEADER) ?? undefined);
    }
  });
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenantRole(req, async (session) => {
    const { id } = await params;
    const pool = await prisma.computePool.findFirst({ where: { id, tenantId: session.tenantId } });
    if (!pool) return NextResponse.json({ error: "Not found" }, { status: 404 });

    const inUse = await prisma.instance.count({
      where: {
        computePoolId: id,
        tenantId: session.tenantId,
        status: { in: ["PENDING", "PROVISIONING", "INITIALIZING", "RUNNING"] },
      },
    });
    if (inUse > 0) return NextResponse.json({ error: "计算池仍有运行中的实例，无法删除" }, { status: 409 });

    await prisma.computePool.delete({ where: { id, tenantId: session.tenantId } });
    return NextResponse.json({ ok: true });
  });
}
