import { NextRequest, NextResponse } from "next/server";
import { withTenant } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { parsePageParams } from "@/lib/pagination";

// Instance creation moved to POST /api/instances/deploy (native vSphere deploy,
// doc 33 §6.5). The legacy agent-prompt Template create path was removed here.
export async function GET(req: NextRequest) {
  return withTenant(req, async (session) => {
    const page = parsePageParams(new URL(req.url));
    if ("error" in page) return NextResponse.json({ error: page.error }, { status: 400 });
    const [items, total] = await prisma.$transaction([
      prisma.instance.findMany({
        where: { tenantId: session.tenantId, status: { not: "DELETED" } },
        include: { computePool: { select: { name: true, type: true } } },
        orderBy: { createdAt: "desc" },
        skip: page.skip,
        take: page.take, // #255: 真分页（默认仍 200，行为兼容）
      }),
      prisma.instance.count({
        where: { tenantId: session.tenantId, status: { not: "DELETED" } },
      }),
    ]);
    return NextResponse.json({ items, total, skip: page.skip, take: page.take });
  });
}
