import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import bcrypt from "bcryptjs";

vi.mock("@/lib/prisma", () => ({
  prisma: {
    user: { findUnique: vi.fn(), create: vi.fn() },
  },
}));
vi.mock("@/lib/auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...real,
    createSession: vi.fn().mockResolvedValue("tok-test"),
    verifyPassword: vi.fn(real.verifyPassword),
  };
});
vi.mock("@/lib/logger", () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { prisma } from "@/lib/prisma";
import { verifyPassword as verifyPasswordMock } from "@/lib/auth";
import { clearRateLimits } from "@/lib/rate-limit";
import { POST as login } from "../login/route";
import { POST as register } from "../register/route";

const findUnique = prisma.user.findUnique as ReturnType<typeof vi.fn>;
const verifyPassword = verifyPasswordMock as ReturnType<typeof vi.fn>;
const createUser = prisma.user.create as ReturnType<typeof vi.fn>;

const PASSWORD = "correct-horse-battery";
const HASH = bcrypt.hashSync(PASSWORD, 4);

function user(over: Record<string, unknown> = {}) {
  return {
    id: "u-1",
    email: "alice@x.com",
    name: "Alice",
    passwordHash: HASH,
    memberships: [
      { tenantId: "t-1", role: "OWNER", tenant: { id: "t-1", name: "T", quotaTokensMonth: BigInt(1) } },
    ],
    ...over,
  };
}

function postLogin(body: unknown, ip = "203.0.113.9") {
  return login(
    new NextRequest("http://test/api/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "content-type": "application/json", "x-forwarded-for": ip },
    })
  );
}

function postRegister(body: unknown, ip = "203.0.113.9") {
  return register(
    new NextRequest("http://test/api/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "content-type": "application/json", "x-forwarded-for": ip },
    })
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  clearRateLimits();
});

describe("login (#90 M2/M3/M5)", () => {
  it("happy path sets the session cookie", async () => {
    findUnique.mockResolvedValue(user());
    const res = await postLogin({ email: "alice@x.com", password: PASSWORD });
    expect(res.status).toBe(200);
    expect(res.cookies.get("session")?.value).toBe("tok-test");
  });

  it("SEC-4: session cookie is httpOnly+lax always, Secure only in production", async () => {
    findUnique.mockResolvedValue(user());
    const prev = process.env.NODE_ENV;
    try {
      // dev: Secure off so http://localhost login works
      vi.stubEnv("NODE_ENV", "development");
      let res = await postLogin({ email: "alice@x.com", password: PASSWORD });
      let c = res.cookies.get("session");
      expect(c?.httpOnly).toBe(true);
      expect(c?.sameSite).toBe("lax");
      expect(c?.secure).toBeFalsy();
      // production: Secure on so the token never rides a plaintext hop
      clearRateLimits();
      vi.stubEnv("NODE_ENV", "production");
      res = await postLogin({ email: "alice@x.com", password: PASSWORD });
      c = res.cookies.get("session");
      expect(c?.secure).toBe(true);
    } finally {
      vi.stubEnv("NODE_ENV", prev ?? "test");
    }
  });

  it("M3: email is case-normalized — Alice@X.com logs in as alice@x.com", async () => {
    findUnique.mockResolvedValue(user());
    const res = await postLogin({ email: "Alice@X.com", password: PASSWORD });
    expect(res.status).toBe(200);
    expect(findUnique.mock.calls[0][0].where.email).toBe("alice@x.com");
  });

  it("M2: a valid password with no memberships returns the SAME generic 401", async () => {
    findUnique.mockResolvedValue(user({ memberships: [] }));
    const res = await postLogin({ email: "alice@x.com", password: PASSWORD });
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.error).toBe("邮箱或密码错误"); // indistinguishable from bad password
  });

  it("#239: unknown email still burns a bcrypt compare (timing oracle)", async () => {
    // user-not-found used to return in ~0ms while wrong-password took a
    // cost-12 compare (~100-300ms) — measurably distinguishable. The route
    // must compare against a static dummy hash when the user is missing.
    findUnique.mockResolvedValue(null);
    const res = await postLogin({ email: "nobody@x.com", password: "whatever-pw" });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("邮箱或密码错误");
    expect(verifyPassword).toHaveBeenCalledTimes(1);
    const [, hashArg] = verifyPassword.mock.calls[0];
    expect(hashArg).toMatch(/^\$2[aby]\$12\$/); // real cost-12 bcrypt hash
  });

  it("wrong password is 401 with the same message", async () => {
    findUnique.mockResolvedValue(user());
    const res = await postLogin({ email: "alice@x.com", password: "nope-nope-nope" });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("邮箱或密码错误");
  });

  it("M5: 6th attempt within the window is 429 with Retry-After", async () => {
    findUnique.mockResolvedValue(user());
    for (let i = 0; i < 5; i++) {
      await postLogin({ email: "alice@x.com", password: "wrong-wrong-wrong" });
    }
    const res = await postLogin({ email: "alice@x.com", password: PASSWORD });
    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBeTruthy();
  });

  it("M5: rate limit key includes the email — another account from same IP still works", async () => {
    findUnique.mockResolvedValue(user());
    for (let i = 0; i < 5; i++) {
      await postLogin({ email: "alice@x.com", password: "wrong-wrong-wrong" });
    }
    const res = await postLogin({ email: "bob@x.com", password: PASSWORD });
    expect(res.status).not.toBe(429);
  });
});

  it("HIGH-1: per-email cap holds even when the attacker rotates fake IPs", async () => {
    findUnique.mockResolvedValue(user());
    let last: Response | undefined;
    for (let i = 0; i < 31; i++) {
      last = await postLogin(
        { email: "alice@x.com", password: "wrong-wrong-wrong" },
        `10.0.${Math.floor(i / 250)}.${i % 250}` // fresh XFF every attempt
      );
    }
    expect(last!.status).toBe(429);
  });

describe("register (#90 M3/M4/M5)", () => {
  const valid = {
    name: "Bob",
    email: "Bob@X.com",
    password: "a-long-enough-password",
    tenantName: "bobco",
  };

  it("M4: passwords shorter than 10 chars are rejected", async () => {
    const res = await postRegister({ ...valid, password: "short6" });
    expect(res.status).toBe(400);
  });

  it("M3: stores the email lowercased", async () => {
    findUnique.mockResolvedValue(null);
    createUser.mockResolvedValue({
      id: "u-2",
      memberships: [{ tenantId: "t-2", tenant: { id: "t-2" } }],
    });
    const res = await postRegister(valid);
    expect(res.status).toBe(200);
    expect(createUser.mock.calls[0][0].data.email).toBe("bob@x.com");
    expect(findUnique.mock.calls[0][0].where.email).toBe("bob@x.com");
  });

  it("MEDIUM-2: validation failure returns a STRING error (page renders it)", async () => {
    const res = await postRegister({ ...valid, password: "short6" });
    const body = await res.json();
    expect(typeof body.error).toBe("string");
  });

  it("LOW-1: validation failures do not burn the register rate budget", async () => {
    findUnique.mockResolvedValue(null);
    createUser.mockResolvedValue({
      id: "u-2",
      memberships: [{ tenantId: "t-2", tenant: { id: "t-2" } }],
    });
    for (let i = 0; i < 5; i++) {
      await postRegister({ ...valid, password: "short6" }); // 400s
    }
    const res = await postRegister({ ...valid, email: "ok@x.com" });
    expect(res.status).toBe(200); // budget untouched by the fumbles
  });

  it("M5: register is rate-limited per IP", async () => {
    findUnique.mockResolvedValue(null);
    createUser.mockResolvedValue({
      id: "u-2",
      memberships: [{ tenantId: "t-2", tenant: { id: "t-2" } }],
    });
    for (let i = 0; i < 5; i++) {
      await postRegister({ ...valid, email: `u${i}@x.com` });
    }
    const res = await postRegister({ ...valid, email: "u9@x.com" });
    expect(res.status).toBe(429);
  });
});

describe("malformed body → 400 not 500 (#357 item 4)", () => {
  function rawRequest(url: string, raw: string) {
    return new NextRequest(url, {
      method: "POST",
      body: raw,
      headers: { "content-type": "application/json", "x-forwarded-for": "203.0.113.50" },
    });
  }

  it("login: non-JSON body is a 400", async () => {
    const res = await login(rawRequest("http://test/api/auth/login", "this is not json"));
    expect(res.status).toBe(400);
    expect(typeof (await res.json()).error).toBe("string");
  });

  it("register: non-JSON body is a 400", async () => {
    const res = await register(rawRequest("http://test/api/auth/register", "}{"));
    expect(res.status).toBe(400);
    expect(typeof (await res.json()).error).toBe("string");
  });
});
