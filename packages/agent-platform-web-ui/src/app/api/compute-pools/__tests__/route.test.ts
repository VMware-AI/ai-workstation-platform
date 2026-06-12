import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/tenant", () => ({
  withTenant: (_req: unknown, fn: (s: { tenantId: string }) => unknown) => fn({ tenantId: "t-1" }),
  withTenantRole: (_req: unknown, fn: (s: { tenantId: string }) => unknown) => fn({ tenantId: "t-1" }),
}));
vi.mock("@/lib/prisma", () => ({
  prisma: { computePool: { findMany: vi.fn(), create: vi.fn() } },
}));
vi.mock("@/lib/crypto", () => ({
  encrypt: vi.fn((v: string) => `enc:${v}`),
  decrypt: vi.fn((v: string) => v.replace(/^enc:/, "")),
}));

import { prisma } from "@/lib/prisma";
import { POST } from "../route";

const create = prisma.computePool.create as ReturnType<typeof vi.fn>;

function post(body: Record<string, unknown>) {
  return POST(
    new NextRequest("http://test/api/compute-pools", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  create.mockResolvedValue({ id: "pool-1" });
});

describe("POST /api/compute-pools — docker pools removed (#90 M14)", () => {
  it("creating a docker pool is a teaching 400, not a provision-time death", async () => {
    const res = await post({ name: "local", type: "docker" });
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toMatch(/docker.*no longer supported/i);
    expect(create).not.toHaveBeenCalled();
  });

  it("vsphere pool creation still works", async () => {
    const res = await post({
      name: "vc1",
      type: "vsphere",
      config: {
        host: "vc1.example.invalid",
        username: "svc",
        password: "pw",
        datacenter: "DC1",
      },
    });
    expect(res.status).not.toBe(400);
    expect(create).toHaveBeenCalled();
  });

  it("malformed body is a 400, not a 500 (#357 item 4)", async () => {
    const res = await POST(
      new NextRequest("http://test/api/compute-pools", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "not json at all",
      })
    );
    expect(res.status).toBe(400);
    expect(create).not.toHaveBeenCalled();
  });
});
