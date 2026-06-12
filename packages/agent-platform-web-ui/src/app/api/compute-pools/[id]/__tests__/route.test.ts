import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/tenant", () => ({
  withTenant: (_req: unknown, fn: (s: { tenantId: string }) => unknown) => fn({ tenantId: "t-1" }),
  withTenantRole: (_req: unknown, fn: (s: { tenantId: string }) => unknown) => fn({ tenantId: "t-1" }),
}));
vi.mock("@/lib/prisma", () => ({
  prisma: { computePool: { findFirst: vi.fn(), update: vi.fn() } },
}));
// Keep encryption out of the test — assert on the config that reaches storage.
vi.mock("@/lib/pool-config", () => ({
  encryptPoolConfigForStorage: vi.fn((incoming: Record<string, unknown>) => ({
    ...incoming,
    passwordEncrypted: incoming.password ? `enc:${String(incoming.password)}` : undefined,
  })),
  redactPoolConfigForWire: vi.fn((c: Record<string, unknown>) => c),
}));

import { prisma } from "@/lib/prisma";
import { encryptPoolConfigForStorage } from "@/lib/pool-config";
import { PATCH } from "../route";

const findFirst = prisma.computePool.findFirst as ReturnType<typeof vi.fn>;
const update = prisma.computePool.update as ReturnType<typeof vi.fn>;
const encryptMock = encryptPoolConfigForStorage as ReturnType<typeof vi.fn>;

function patch(body: unknown, raw = false) {
  return PATCH(
    new NextRequest("http://test/api/compute-pools/pool-1", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: raw ? (body as string) : JSON.stringify(body),
    }),
    { params: Promise.resolve({ id: "pool-1" }) }
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  findFirst.mockResolvedValue({ id: "pool-1", tenantId: "t-1", config: {} });
  update.mockResolvedValue({ id: "pool-1", config: {} });
});

describe("PATCH /api/compute-pools/[id]", () => {
  it("malformed body is a 400, not a 500 (#357 item 4)", async () => {
    const res = await patch("not json at all", true);
    expect(res.status).toBe(400);
    expect(update).not.toHaveBeenCalled();
  });

  it("config now uses the strict vsphere schema — unknown keys are rejected (#357 item 5)", async () => {
    const res = await patch({ config: { host: "vc.invalid", evilKey: "x" } });
    expect(res.status).toBe(400);
    expect(update).not.toHaveBeenCalled();
  });

  it("a partial config update (single known field) is accepted", async () => {
    const res = await patch({ config: { datacenter: "DC2" } });
    expect(res.status).toBe(200);
    expect(update).toHaveBeenCalled();
  });

  it("the password sentinel is accepted (kept-current path)", async () => {
    const res = await patch({ config: { password: "****" } });
    expect(res.status).toBe(200);
    expect(encryptMock).toHaveBeenCalled();
  });

  it("toggling enabled only (no config) still works", async () => {
    const res = await patch({ enabled: false });
    expect(res.status).toBe(200);
    expect(update).toHaveBeenCalled();
  });

  it("an empty PATCH body is valid (all fields optional)", async () => {
    const res = await patch({});
    expect(res.status).toBe(200);
  });
});
