import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/tenant", () => ({
  withTenantRole: (_req: unknown, fn: (s: { tenantId: string }) => unknown) =>
    fn({ tenantId: "t-1" }),
}));
vi.mock("@/lib/prisma", () => ({
  prisma: {
    computePool: { findFirst: vi.fn() },
    instance: { update: vi.fn() },
    $transaction: vi.fn(),
  },
}));
vi.mock("@/lib/queue", () => ({
  enqueueProvision: vi.fn(),
  countProvisionWorkers: vi.fn(),
}));
vi.mock("@/lib/crypto", () => ({ encrypt: vi.fn((v: string) => `enc(${v})`) }));
vi.mock("@/lib/providers/vsphere/cloudinit/deploy-params", () => ({
  buildInstancesFromDeploy: vi.fn(),
  SECRET_DEPLOY_FIELDS: [],
}));

import { prisma } from "@/lib/prisma";
import { enqueueProvision, countProvisionWorkers } from "@/lib/queue";
import { buildInstancesFromDeploy } from "@/lib/providers/vsphere/cloudinit/deploy-params";
import { POST } from "../route";

const poolFindFirst = prisma.computePool.findFirst as ReturnType<typeof vi.fn>;
const $transaction = prisma.$transaction as ReturnType<typeof vi.fn>;
const enqueue = enqueueProvision as ReturnType<typeof vi.fn>;
const workerCount = countProvisionWorkers as ReturnType<typeof vi.fn>;
const build = buildInstancesFromDeploy as ReturnType<typeof vi.fn>;

const txCount = vi.fn();
const txCreate = vi.fn();

function post() {
  return POST(
    new NextRequest("http://test/api/instances/deploy", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        computePoolId: "pool-1",
        mode: "single",
        shared: { templateName: "ubuntu-22" },
        vm: {
          name: "vm-1",
          network: { mode: "dhcp" },
          osUser: "agent",
          osPassword: "pw",
        },
      }),
    })
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  poolFindFirst.mockResolvedValue({ id: "pool-1", type: "vsphere" });
  build.mockReturnValue({
    errors: [],
    instances: [{ name: "vm-1", deployParams: {} }],
  });
  txCreate.mockResolvedValue({ id: "inst-1", name: "vm-1" });
  $transaction.mockImplementation(async (fn: (tx: unknown) => unknown) =>
    fn({
      $queryRaw: vi.fn().mockResolvedValue([]),
      tenant: { findUnique: vi.fn().mockResolvedValue({ id: "t-1", quotaInstances: 5 }) },
      instance: { count: txCount, create: txCreate },
    })
  );
  txCount.mockResolvedValue(0);
  enqueue.mockResolvedValue(undefined);
  workerCount.mockResolvedValue(1);
});

describe("POST /api/instances/deploy — quota counts every live state", () => {
  it("counts PENDING + PROVISIONING + INITIALIZING + RUNNING (not just PENDING/RUNNING)", async () => {
    const res = await post();
    expect(res.status).toBe(201);

    const statuses = txCount.mock.calls[0][0].where.status.in as string[];
    expect([...statuses].sort()).toEqual(
      ["INITIALIZING", "PENDING", "PROVISIONING", "RUNNING"].sort()
    );
  });

  it("in-flight PROVISIONING/INITIALIZING instances can no longer slip past quota", async () => {
    // 5 live instances (e.g. all mid-provision) against a quota of 5: one more
    // must be rejected, where the old PENDING/RUNNING-only count saw 0.
    txCount.mockResolvedValue(5);

    const res = await post();

    expect(res.status).toBe(429);
    expect(txCreate).not.toHaveBeenCalled();
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("malformed body is a 400, not a 500 (#357 item 4)", async () => {
    const res = await POST(
      new NextRequest("http://test/api/instances/deploy", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "not json at all",
      })
    );
    expect(res.status).toBe(400);
    expect(txCreate).not.toHaveBeenCalled();
  });
});
