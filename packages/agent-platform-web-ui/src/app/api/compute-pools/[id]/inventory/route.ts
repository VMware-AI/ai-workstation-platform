import { NextRequest, NextResponse } from "next/server";
import { withTenantRole } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { decryptPoolPassword } from "@/lib/pool-config";
import { listInventory, GovcError, type VcConn } from "@/lib/providers/vsphere/govc";
import { teachVcError } from "@/lib/providers/vsphere/teach-vc-error";
import { logger } from "@/lib/logger";

// List clone-time placement resources (datastore / network / resource pool /
// folder / VM template) live from a saved pool's vCenter, for the deploy form's
// dropdowns. Uses the pool's stored (encrypted) credentials — the browser never
// sends them. Read-only.
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenantRole(req, async (session) => {
    const { id } = await params;
    const pool = await prisma.computePool.findFirst({
      where: { id, tenantId: session.tenantId },
    });
    if (!pool) return NextResponse.json({ error: "计算池不存在" }, { status: 404 });
    if (pool.type !== "vsphere") {
      return NextResponse.json({ error: "仅 vSphere 计算池支持资源列举。" }, { status: 400 });
    }

    const cfg = (pool.config ?? {}) as Record<string, unknown>;
    const conn: VcConn = {
      host: String(cfg.host ?? ""),
      username: String(cfg.username ?? ""),
      password: decryptPoolPassword(cfg),
      datacenter: cfg.datacenter ? String(cfg.datacenter) : undefined,
      verifySsl: cfg.verifySsl === false ? false : undefined,
    };

    try {
      const inventory = await listInventory(conn);
      return NextResponse.json({ ok: true, inventory });
    } catch (e) {
      // GovcError messages are already mapped (teachGovc → teachVcError) and
      // safe; for any other error map it too so raw govc stderr / internal
      // detail never reaches the browser. Full detail goes to the server log.
      const raw = e instanceof Error ? e.message : String(e);
      logger.error("compute-pool inventory failed", { scope: "compute-pools", poolId: id, error: raw });
      const error = e instanceof GovcError ? e.message : teachVcError(raw);
      return NextResponse.json({ ok: false, error });
    }
  });
}
