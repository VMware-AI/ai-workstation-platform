import { NextRequest, NextResponse } from "next/server";
import { withTenant } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { resolveProvider } from "@/lib/providers";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenant(req, async (session) => {
    const { id } = await params;
    const instance = await prisma.instance.findFirst({
      where: { id, tenantId: session.tenantId },
      include: { computePool: { select: { type: true } } },
    });
    if (!instance) return NextResponse.json({ error: "Not found" }, { status: 404 });

    // #90 M11 pattern: a non-numeric ?hours= gave NaN → Invalid Date → Prisma
    // 500. Parse, default 1, clamp to [1, 168] (one week).
    const url = new URL(req.url);
    const parsedHours = Number.parseInt(url.searchParams.get("hours") ?? "1", 10);
    const hours = Number.isFinite(parsedHours) ? Math.min(Math.max(parsedHours, 1), 168) : 1;
    const since = new Date(Date.now() - hours * 60 * 60 * 1000);

    const historical = await prisma.metric.findMany({
      where: { instanceId: id, timestamp: { gte: since } },
      orderBy: { timestamp: "asc" },
      take: 200,
    });

    let current = null;
    const provider = resolveProvider(instance.computePool?.type);
    if (provider && instance.status === "RUNNING") {
      current = await provider.getStats(id, instance.vmRefId ?? undefined);
      // Persist current metric
      await prisma.metric.create({
        data: { instanceId: id, ...current },
      }).catch(() => {});
    }

    return NextResponse.json({ historical, current });
  });
}
