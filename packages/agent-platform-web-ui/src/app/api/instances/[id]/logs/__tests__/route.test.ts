import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/tenant", () => ({
  withTenant: (_req: unknown, fn: (s: { tenantId: string }) => unknown) =>
    fn({ tenantId: "t-1" }),
}));
vi.mock("@/lib/prisma", () => ({
  prisma: { instance: { findFirst: vi.fn() }, logEntry: { findMany: vi.fn() } },
}));
vi.mock("@/lib/providers", () => ({ resolveProvider: vi.fn(() => null) }));

import { prisma } from "@/lib/prisma";
import { GET } from "../route";

const findFirst = prisma.instance.findFirst as ReturnType<typeof vi.fn>;
const findMany = prisma.logEntry.findMany as ReturnType<typeof vi.fn>;

function get(query: string) {
  const req = new NextRequest(`http://test/api/instances/inst-1/logs${query}`);
  return GET(req, { params: Promise.resolve({ id: "inst-1" }) });
}

function takeArg(): number {
  return findMany.mock.calls[0][0].take;
}

beforeEach(() => {
  vi.clearAllMocks();
  findFirst.mockResolvedValue({
    id: "inst-1",
    tenantId: "t-1",
    status: "STOPPED",
    vmRefId: null,
    computePool: { type: "vsphere" },
  });
  findMany.mockResolvedValue([]);
});

describe("GET /api/instances/[id]/logs — ?limit= is parsed, defaulted, clamped (#90 M11 pattern)", () => {
  it("defaults to 50 when limit is missing", async () => {
    const res = await get("");
    expect(res.status).toBe(200);
    expect(takeArg()).toBe(50);
  });

  it("a non-numeric limit falls back to 50 instead of NaN → 500", async () => {
    const res = await get("?limit=abc");
    expect(res.status).toBe(200);
    expect(takeArg()).toBe(50);
  });

  it("clamps out-of-range values into [1, 500]", async () => {
    await get("?limit=99999");
    expect(takeArg()).toBe(500);
    findMany.mockClear();
    await get("?limit=-5");
    expect(takeArg()).toBe(1);
  });

  it("passes a valid in-range limit through", async () => {
    await get("?limit=200");
    expect(takeArg()).toBe(200);
  });
});
