import { NextRequest, NextResponse } from "next/server";
import { withTenant } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenant(req, async (session) => {
    const { id } = await params;
    const instance = await prisma.instance.findFirst({ where: { id, tenantId: session.tenantId } });
    if (!instance) return NextResponse.json({ error: "Not found" }, { status: 404 });

    const events = await prisma.provisionEvent.findMany({
      where: { instanceId: id },
      orderBy: { createdAt: "asc" },
    });
    return NextResponse.json(events);
  });
}
