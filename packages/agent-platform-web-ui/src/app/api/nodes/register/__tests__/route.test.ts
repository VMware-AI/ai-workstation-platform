import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import crypto from "crypto";

vi.mock("@/lib/prisma", () => ({
  prisma: {
    instance: { findUnique: vi.fn(), updateMany: vi.fn(), update: vi.fn() },
    provisionEvent: { create: vi.fn() },
  },
}));

import { prisma } from "@/lib/prisma";
import { POST } from "../route";

const findUnique = prisma.instance.findUnique as ReturnType<typeof vi.fn>;
const updateMany = prisma.instance.updateMany as ReturnType<typeof vi.fn>;
const update = prisma.instance.update as ReturnType<typeof vi.fn>;
const createEvent = prisma.provisionEvent.create as ReturnType<typeof vi.fn>;

const TOKEN = "agent-token-1";
const HASH = crypto.createHash("sha256").update(TOKEN).digest("hex");

function row(over: Record<string, unknown> = {}) {
  return {
    id: "inst-1",
    status: "PROVISIONING",
    agentTokenHash: HASH,
    agentTokenExpiresAt: new Date(Date.now() + 60 * 60 * 1000),
    ...over,
  };
}

function post(body: Record<string, unknown> = {}) {
  return POST(
    new NextRequest("http://test/api/nodes/register", {
      method: "POST",
      headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify({ instanceId: "inst-1", status: "READY", ...body }),
    })
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  updateMany.mockResolvedValue({ count: 1 });
  update.mockResolvedValue({});
  createEvent.mockResolvedValue({});
});

describe("POST /api/nodes/register — guarded RUNNING write (#244)", () => {
  it("registers from PROVISIONING via a guarded transition (no plain update)", async () => {
    findUnique.mockResolvedValue(row());
    const res = await post({ ipAddress: "10.0.0.9" });
    expect(res.status).toBe(200);
    expect(update).not.toHaveBeenCalled();
    const call = updateMany.mock.calls[0][0];
    expect(call.where.status.in).not.toContain("DELETED");
    expect(call.where.status.in).not.toContain("STOPPING");
    expect(call.data).toMatchObject({ status: "RUNNING", agentTokenHash: null });
  });

  it("re-registration from RUNNING is idempotent (cloud-init may fire twice)", async () => {
    findUnique.mockResolvedValue(row({ status: "RUNNING" }));
    const res = await post({ ipAddress: "10.0.0.10" });
    expect(res.status).toBe(200);
    const call = updateMany.mock.calls[0][0];
    expect(call.where.status.in).toContain("RUNNING");
    expect(call.data).toMatchObject({ status: "RUNNING", ipAddress: "10.0.0.10" });
  });

  it("a hash with NO expiry is rejected — never-dying tokens fail closed (PR #253 L-4)", async () => {
    findUnique.mockResolvedValue(row({ agentTokenExpiresAt: null }));
    const res = await post();
    expect(res.status).toBe(401);
    expect(updateMany).not.toHaveBeenCalled();
  });

  it("a row a stop/delete already owns is NOT flipped back to RUNNING — 409", async () => {
    // Late-arriving agent call: the guard misses (row is STOPPING/DELETED).
    findUnique.mockResolvedValue(row({ status: "STOPPING" }));
    updateMany.mockResolvedValue({ count: 0 });
    const res = await post();
    expect(res.status).toBe(409);
  });
});
