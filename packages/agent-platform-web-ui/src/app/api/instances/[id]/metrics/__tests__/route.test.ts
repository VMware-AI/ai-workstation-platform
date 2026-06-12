import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/tenant", () => ({
  withTenant: (_req: unknown, fn: (s: { tenantId: string }) => unknown) =>
    fn({ tenantId: "t-1" }),
}));
vi.mock("@/lib/prisma", () => ({
  prisma: {
    instance: { findFirst: vi.fn() },
    metric: { findMany: vi.fn(), create: vi.fn() },
  },
}));
vi.mock("@/lib/providers", () => ({ resolveProvider: vi.fn(() => null) }));

import { prisma } from "@/lib/prisma";
import { GET } from "../route";

const findFirst = prisma.instance.findFirst as ReturnType<typeof vi.fn>;
const findMany = prisma.metric.findMany as ReturnType<typeof vi.fn>;

const NOW = new Date("2026-06-11T12:00:00Z");
const HOUR = 60 * 60 * 1000;

function get(query: string) {
  const req = new NextRequest(`http://test/api/instances/inst-1/metrics${query}`);
  return GET(req, { params: Promise.resolve({ id: "inst-1" }) });
}

// Hours actually used = how far back `since` lies from the frozen clock.
function hoursUsed(): number {
  const since = findMany.mock.calls[0][0].where.timestamp.gte as Date;
  return (NOW.getTime() - since.getTime()) / HOUR;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  findFirst.mockResolvedValue({
    id: "inst-1",
    tenantId: "t-1",
    status: "STOPPED",
    vmRefId: null,
    computePool: { type: "vsphere" },
  });
  findMany.mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("GET /api/instances/[id]/metrics — ?hours= is parsed, defaulted, clamped (#90 M11 pattern)", () => {
  it("defaults to 1 hour when hours is missing", async () => {
    const res = await get("");
    expect(res.status).toBe(200);
    expect(hoursUsed()).toBe(1);
  });

  it("a non-numeric hours falls back to 1 instead of NaN → Invalid Date → 500", async () => {
    const res = await get("?hours=abc");
    expect(res.status).toBe(200);
    expect(hoursUsed()).toBe(1);
  });

  it("clamps out-of-range values into [1, 168]", async () => {
    await get("?hours=99999");
    expect(hoursUsed()).toBe(168);
    findMany.mockClear();
    await get("?hours=-3");
    expect(hoursUsed()).toBe(1);
  });

  it("passes a valid in-range hours through", async () => {
    await get("?hours=24");
    expect(hoursUsed()).toBe(24);
  });
});
