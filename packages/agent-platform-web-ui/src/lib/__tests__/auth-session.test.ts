import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import crypto from "crypto";

vi.mock("@/lib/prisma", () => ({
  prisma: {
    session: { findUnique: vi.fn(), delete: vi.fn(), update: vi.fn() },
  },
}));

import { prisma } from "@/lib/prisma";
import { getSession, SESSION_IDLE_MS } from "../auth";

const findUnique = prisma.session.findUnique as ReturnType<typeof vi.fn>;
const del = prisma.session.delete as ReturnType<typeof vi.fn>;
const update = prisma.session.update as ReturnType<typeof vi.fn>;

const TOKEN = "tok-abc";
const TOKEN_HASH = crypto.createHash("sha256").update(TOKEN).digest("hex");

function sessionRow(over: Record<string, unknown> = {}) {
  const now = Date.now();
  return {
    tokenHash: TOKEN_HASH,
    tenantId: "t-1",
    expiresAt: new Date(now + 24 * 60 * 60 * 1000),
    lastSeenAt: new Date(now),
    user: {
      id: "u-1",
      email: "alice@x.com",
      name: "Alice",
      memberships: [{ tenantId: "t-1", role: "OWNER" }],
    },
    ...over,
  };
}

function reqWithCookie() {
  return new NextRequest("http://test/api/anything", {
    headers: { cookie: `session=${TOKEN}` },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  del.mockResolvedValue({});
  update.mockResolvedValue({});
});

describe("session idle window (#90 M1)", () => {
  it("a warm session resolves and is not touched (<1h idle)", async () => {
    findUnique.mockResolvedValue(sessionRow());
    const s = await getSession(reqWithCookie());
    expect(s?.userId).toBe("u-1");
    expect(update).not.toHaveBeenCalled();
    expect(del).not.toHaveBeenCalled();
  });

  it("idle >24h is rejected and the session row deleted", async () => {
    findUnique.mockResolvedValue(
      sessionRow({ lastSeenAt: new Date(Date.now() - SESSION_IDLE_MS - 60_000) })
    );
    const s = await getSession(reqWithCookie());
    expect(s).toBeNull();
    expect(del).toHaveBeenCalledWith({ where: { tokenHash: TOKEN_HASH } });
  });

  it("idle between 1h and 24h resolves AND bumps lastSeenAt (throttled keep-alive)", async () => {
    findUnique.mockResolvedValue(
      sessionRow({ lastSeenAt: new Date(Date.now() - 2 * 60 * 60 * 1000) })
    );
    const s = await getSession(reqWithCookie());
    expect(s?.userId).toBe("u-1");
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { tokenHash: TOKEN_HASH },
        data: expect.objectContaining({ lastSeenAt: expect.any(Date) }),
      })
    );
  });

  it("absolute expiry still wins even when recently active", async () => {
    findUnique.mockResolvedValue(
      sessionRow({ expiresAt: new Date(Date.now() - 1000), lastSeenAt: new Date() })
    );
    const s = await getSession(reqWithCookie());
    expect(s).toBeNull();
    expect(del).toHaveBeenCalled();
  });
});
