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

    // DB logs. #90 M11 pattern: a non-numeric ?limit= gave NaN → Prisma 500.
    // Parse, default 50, clamp to [1, 500].
    const url = new URL(req.url);
    const parsedLimit = Number.parseInt(url.searchParams.get("limit") ?? "50", 10);
    const limit = Number.isFinite(parsedLimit) ? Math.min(Math.max(parsedLimit, 1), 500) : 50;
    const dbLogs = await prisma.logEntry.findMany({
      where: { instanceId: id },
      orderBy: { timestamp: "desc" },
      take: limit,
    });

    // Live VM/agent logs if running (provider-sourced; empty until agent
    // telemetry lands — doc 33 §5.5 / OQ-4).
    let containerLogs: string[] = [];
    const provider = resolveProvider(instance.computePool?.type);
    if (provider && instance.status === "RUNNING") {
      containerLogs = await provider.getLogs(id, instance.vmRefId ?? undefined, 50);
    }

    return NextResponse.json({ dbLogs: dbLogs.reverse(), containerLogs });
  });
}
