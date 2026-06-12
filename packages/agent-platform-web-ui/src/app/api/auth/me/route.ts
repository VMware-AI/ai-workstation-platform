import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

export async function GET(req: NextRequest) {
  const session = await getSession(req);
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const tenant = await prisma.tenant.findUnique({ where: { id: session.tenantId } });
  return NextResponse.json({
    user: session,
    tenant: tenant
      ? { ...tenant, quotaTokensMonth: tenant.quotaTokensMonth.toString() }
      : null,
  });
}
