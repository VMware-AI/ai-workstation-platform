import { NextRequest, NextResponse } from "next/server";
import { getSession, SessionUser } from "./auth";

// CONTRACT (#90 M6): withTenant/withTenantRole AUTHENTICATE — they do NOT
// inject a tenant filter into Prisma queries. Every query on a tenant-scoped
// model (Instance, ComputePool, ModelProvider, UsageRecord, BillingRecord,
// Session, Membership) must carry tenantId in its where/data itself. This is
// enforced by src/lib/__tests__/tenant-scope-guard.test.ts — a route that
// forgets the filter fails CI, not production. Legitimately tenant-free
// calls (bearer-token agent endpoints) carry a "// tenant-scope:" comment.

export async function withTenant(
  req: NextRequest,
  handler: (session: SessionUser) => Promise<NextResponse>
): Promise<NextResponse> {
  const session = await getSession(req);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  return handler(session);
}

// Roles allowed to mutate shared tenant resources (templates / model-providers
// / compute-pools). MEMBER is read-only on shared resources (#90 M7).
export const MANAGE_ROLES = ["OWNER", "ADMIN"] as const;

// Authenticated + role-gated. Wraps withTenant and rejects with 403 when the
// session's role is not in allowedRoles (default: OWNER/ADMIN). Fixes #90 M7 —
// previously any tenant MEMBER could mutate/delete shared resources.
export async function withTenantRole(
  req: NextRequest,
  handler: (session: SessionUser) => Promise<NextResponse>,
  allowedRoles: readonly string[] = MANAGE_ROLES
): Promise<NextResponse> {
  return withTenant(req, async (session) => {
    if (!allowedRoles.includes(session.role)) {
      return NextResponse.json(
        { error: `Forbidden: requires role ${allowedRoles.join(" or ")}` },
        { status: 403 }
      );
    }
    return handler(session);
  });
}
