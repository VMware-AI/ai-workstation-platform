import { NextRequest, NextResponse } from "next/server";
import { withTenant } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { parsePageParams } from "@/lib/pagination";

export async function GET(req: NextRequest) {
  return withTenant(req, async (session) => {
    const url = new URL(req.url);
    // #90 M11: a non-numeric ?days= gave NaN → Invalid Date → Prisma 500.
    // Parse, default 30, clamp to [1, 365].
    const parsedDays = Number.parseInt(url.searchParams.get("days") ?? "30", 10);
    const days = Number.isFinite(parsedDays) ? Math.min(Math.max(parsedDays, 1), 365) : 30;
    const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

    const page = parsePageParams(url, { maxTake: 500 });
    if ("error" in page) return NextResponse.json({ error: page.error }, { status: 400 });
    // #255: totals 改为全窗口聚合——分页后再按页内 reduce 会把汇总数算错
    const [records, agg] = await prisma.$transaction([
      prisma.usageRecord.findMany({
        where: { tenantId: session.tenantId, timestamp: { gte: since } },
        include: { instance: { select: { name: true } } },
        orderBy: { timestamp: "desc" },
        skip: page.skip,
        take: page.take,
      }),
      prisma.usageRecord.aggregate({
        where: { tenantId: session.tenantId, timestamp: { gte: since } },
        _sum: { totalTokens: true, costCents: true },
        _count: true,
      }),
    ]);
    const totals = {
      totalTokens: agg._sum.totalTokens ?? 0,
      totalCostCents: agg._sum.costCents ?? 0,
      count: agg._count,
    };

    return NextResponse.json({
      records,
      totals,
      total: agg._count,
      skip: page.skip,
      take: page.take,
    });
  });
}
