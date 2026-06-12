import { cache } from "react";
import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import bcrypt from "bcryptjs";
import crypto from "crypto";
import { prisma } from "./prisma";

export interface SessionUser {
  userId: string;
  email: string;
  name: string | null;
  tenantId: string;
  role: string;
}

export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, 12);
}

export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  return bcrypt.compare(password, hash);
}

// Session tokens are stored as sha256 hex digests at rest. The plain
// token only lives in the user's `session` cookie. A read-only leak of
// the Session table therefore yields hashes, not bearer credentials.
function hashSessionToken(token: string): string {
  return crypto.createHash("sha256").update(token).digest("hex");
}

// Session lifetime constants live in session-config.ts (#239: shared with
// the worker's session sweep without dragging react/next into its bundle).
import {
  SESSION_ABSOLUTE_MS,
  SESSION_IDLE_MS,
  SESSION_TOUCH_INTERVAL_MS,
} from "./session-config";
export { SESSION_ABSOLUTE_MS, SESSION_IDLE_MS } from "./session-config";

// #239 timing oracle: when login hits an unknown email, compare against this
// static cost-12 hash so the response takes as long as a real bcrypt check.
// (Hash of a fixed non-secret string; never matches any user password
// because verifyPassword still requires the user row to exist.)
export const DUMMY_PASSWORD_HASH =
  "$2b$12$GpgP0mOhk2cJ9CLxCrOqF.NvZfZA4PTFIi0Z3SCQPJdACXsHBtYWK";

export async function createSession(userId: string, tenantId: string): Promise<string> {
  const token = crypto.randomBytes(32).toString("hex");
  const tokenHash = hashSessionToken(token);
  const expiresAt = new Date(Date.now() + SESSION_ABSOLUTE_MS);
  // Persist the tenant chosen at login so the session is bound to it (#91 H2),
  // instead of getSession silently falling back to memberships[0].
  await prisma.session.create({ data: { userId, tokenHash, tenantId, expiresAt } });
  return token;
}

// cache() deduplicates calls within a single React render pass (Server Components + layout)
const getSessionCached = cache(async (token: string): Promise<SessionUser | null> => {
  const tokenHash = hashSessionToken(token);
  const session = await prisma.session.findUnique({
    where: { tokenHash },
    include: { user: { include: { memberships: true } } },
  });
  const now = new Date();
  if (!session || session.expiresAt < now) {
    if (session) await prisma.session.delete({ where: { tokenHash } }).catch(() => {});
    return null;
  }
  // Sliding idle window (#90 M1).
  const idleMs = now.getTime() - session.lastSeenAt.getTime();
  if (idleMs > SESSION_IDLE_MS) {
    await prisma.session.delete({ where: { tokenHash } }).catch(() => {});
    return null;
  }
  if (idleMs > SESSION_TOUCH_INTERVAL_MS) {
    // Throttled keep-alive bump; fire-and-forget — a failed touch must not
    // fail the request (worst case the session idles out a little early).
    prisma.session.update({ where: { tokenHash }, data: { lastSeenAt: now } }).catch(() => {});
  }
  // Resolve the membership for the tenant this session is bound to (#91 H2).
  // Falls back to memberships[0] only for legacy sessions created before the
  // tenantId column existed (empty string).
  const membership =
    session.user.memberships.find((m) => m.tenantId === session.tenantId) ??
    (session.tenantId ? null : session.user.memberships[0]);
  if (!membership) return null;
  return {
    userId: session.user.id,
    email: session.user.email,
    name: session.user.name,
    tenantId: membership.tenantId,
    role: membership.role,
  };
});

export async function getSession(req?: NextRequest): Promise<SessionUser | null> {
  let token: string | undefined;
  if (req) {
    token = req.cookies.get("session")?.value;
  } else {
    const cookieStore = await cookies();
    token = cookieStore.get("session")?.value;
  }
  if (!token) return null;
  return getSessionCached(token);
}

export async function deleteSession(token: string): Promise<void> {
  const tokenHash = hashSessionToken(token);
  await prisma.session.delete({ where: { tokenHash } }).catch(() => {});
}
