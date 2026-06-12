import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { prisma } from "@/lib/prisma";
import { verifyPassword, createSession, DUMMY_PASSWORD_HASH, SESSION_ABSOLUTE_MS } from "@/lib/auth";
import { rateLimit, clientIp } from "@/lib/rate-limit";
import { logger } from "@/lib/logger";

const schema = z.object({
  // Lowercase at the boundary (#90 M3): Alice@x.com and alice@x.com are the
  // same mailbox; without this, registration casing locked users out.
  email: z
    .string()
    .email()
    .transform((e) => e.toLowerCase()),
  password: z.string().min(1),
});

const GENERIC_401 = "邮箱或密码错误";

export async function POST(req: NextRequest) {
  // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
  const body = await req.json().catch(() => null);
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: "参数无效" }, { status: 400 });
  }

  const { email, password } = parsed.data;

  // #90 M5: bcrypt cost slows one attempt; nothing gated volume. Two layers:
  // - ip+email (tight): one noisy source can't lock an account globally —
  //   but the IP comes from x-forwarded-for, which is client-controlled.
  // - email-only (generous, PR #237 review HIGH-1): bounds account-targeted
  //   brute force even when the attacker rotates fake IPs. Generous enough
  //   that deliberately locking out a victim stays a visible, bounded nuisance.
  const rl = rateLimit(`login:${clientIp(req.headers)}:${email}`, {
    limit: 5,
    windowMs: 15 * 60 * 1000,
  });
  const rlEmail = rateLimit(`login-email:${email}`, {
    limit: 30,
    windowMs: 15 * 60 * 1000,
  });
  if (!rl.allowed || !rlEmail.allowed) {
    const retryAfterS = Math.max(rl.retryAfterS, rlEmail.retryAfterS);
    return NextResponse.json(
      { error: "尝试过于频繁,请稍后再试" },
      { status: 429, headers: { "Retry-After": String(retryAfterS) } }
    );
  }

  const user = await prisma.user.findUnique({
    where: { email },
    include: { memberships: { include: { tenant: true } } },
  });

  // #239 timing oracle: an unknown email must cost the same as a wrong
  // password — compare against a static dummy hash instead of returning
  // in ~0ms while the real path burns a cost-12 bcrypt round.
  const passwordOk = await verifyPassword(password, user?.passwordHash ?? DUMMY_PASSWORD_HASH);
  if (!user || !passwordOk) {
    return NextResponse.json({ error: GENERIC_401 }, { status: 401 });
  }

  if (user.memberships.length === 0) {
    // #90 M2: a distinct "no tenant" response leaked that the password was
    // otherwise correct. Same generic 401 to the caller; the real reason
    // goes to the server log for the operator.
    logger.info("login rejected: user has no memberships", { userId: user.id });
    return NextResponse.json({ error: GENERIC_401 }, { status: 401 });
  }

  const tenantId = user.memberships[0].tenantId;
  const token = await createSession(user.id, tenantId);

  const tenant = user.memberships[0].tenant;
  const res = NextResponse.json({
    ok: true,
    user: { id: user.id, email, name: user.name },
    tenant: { ...tenant, quotaTokensMonth: tenant.quotaTokensMonth.toString() },
  });
  res.cookies.set("session", token, {
    httpOnly: true,
    // Secure in production so the session token never rides a plaintext hop
    // (before HSTS engages, or an internal reverse-proxy leg). Left off in
    // dev so local http://localhost login still works.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_ABSOLUTE_MS / 1000,
  });
  return res;
}
