import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { prisma } from "@/lib/prisma";
import { hashPassword, createSession, SESSION_ABSOLUTE_MS } from "@/lib/auth";
import { rateLimit, clientIp } from "@/lib/rate-limit";

const schema = z.object({
  name: z.string().min(1).max(50),
  // Lowercase at the boundary (#90 M3) — see login/route.ts.
  email: z
    .string()
    .email()
    .transform((e) => e.toLowerCase()),
  // #90 M4: min 6 was trivially brute-forceable. Length over composition
  // rules (NIST 800-63B); 128 cap bounds the bcrypt input.
  password: z
    .string()
    .min(10, "密码至少 10 个字符")
    .max(128),
  tenantName: z.string().min(1).max(50),
});

export async function POST(req: NextRequest) {
  // Parse first (PR #237 review LOW-1, matches login): a user fumbling
  // validation must not burn the rate budget below.
  // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
  const body = await req.json().catch(() => null);
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    // String message (review MEDIUM-2): the register page renders `error`
    // as a React child — an object (zod flatten()) crashes the render.
    const message = parsed.error.issues[0]?.message ?? "参数无效";
    return NextResponse.json({ error: message }, { status: 400 });
  }

  // #90 M5: per-IP — registration spam creates users + tenants.
  const rl = rateLimit(`register:${clientIp(req.headers)}`, {
    limit: 5,
    windowMs: 60 * 60 * 1000,
  });
  if (!rl.allowed) {
    return NextResponse.json(
      { error: "注册过于频繁,请稍后再试" },
      { status: 429, headers: { "Retry-After": String(rl.retryAfterS) } }
    );
  }

  const { name, email, password, tenantName } = parsed.data;

  // Known, accepted enumeration oracle (#239 ①): a distinct 409 tells the
  // caller this email exists. Removing it requires an email-verification
  // flow ("code sent" for both cases) — there is no mail infrastructure
  // yet. Mitigation today: the per-IP rate limit above bounds probing to
  // 5/hour/IP. Revisit when email infra lands.
  const existing = await prisma.user.findUnique({ where: { email } });
  if (existing) {
    return NextResponse.json({ error: "邮箱已注册" }, { status: 409 });
  }

  const slug = tenantName.toLowerCase().replace(/[^a-z0-9]/g, "-") + "-" + Date.now();
  const passwordHash = await hashPassword(password);

  const user = await prisma.user.create({
    data: {
      name,
      email,
      passwordHash,
      memberships: {
        create: {
          role: "OWNER",
          tenant: { create: { name: tenantName, slug } },
        },
      },
    },
    include: { memberships: { include: { tenant: true } } },
  });

  const tenantId = user.memberships[0].tenantId;
  const token = await createSession(user.id, tenantId);

  const res = NextResponse.json({ ok: true, user: { id: user.id, email, name } });
  res.cookies.set("session", token, {
    httpOnly: true,
    // Secure in production — see login/route.ts. Off in dev for http://localhost.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_ABSOLUTE_MS / 1000,
  });
  return res;
}
