import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import crypto from "crypto";

vi.mock("@/lib/prisma", () => ({
  prisma: { instance: { findUnique: vi.fn() } },
}));

import { prisma } from "@/lib/prisma";
import { POST } from "../route";

const findUnique = prisma.instance.findUnique as ReturnType<typeof vi.fn>;

const TOKEN = "agent-token-1";
const HASH = crypto.createHash("sha256").update(TOKEN).digest("hex");

function row(over: Record<string, unknown> = {}) {
  return {
    id: "inst-1",
    agentTokenHash: HASH,
    agentTokenExpiresAt: new Date(Date.now() + 60 * 60 * 1000),
    ...over,
  };
}

function post() {
  return POST(
    new NextRequest("http://test/api/nodes/credentials", {
      method: "POST",
      headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify({ instanceId: "inst-1" }),
    })
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("POST /api/nodes/credentials — token expiry fails closed (parity with /nodes/register, PR #253 L-4)", () => {
  it("a valid, unexpired token passes the auth gate (501 = not-yet-implemented body)", async () => {
    findUnique.mockResolvedValue(row());
    const res = await post();
    expect(res.status).toBe(501);
  });

  it("a hash with NO expiry is rejected — never-dying tokens are not allowed", async () => {
    findUnique.mockResolvedValue(row({ agentTokenExpiresAt: null }));
    const res = await post();
    expect(res.status).toBe(401);
  });

  it("an expired token is rejected", async () => {
    findUnique.mockResolvedValue(
      row({ agentTokenExpiresAt: new Date(Date.now() - 1000) })
    );
    const res = await post();
    expect(res.status).toBe(401);
  });
});
