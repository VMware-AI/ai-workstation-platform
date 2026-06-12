import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/tenant", () => ({
  withTenant: (_req: unknown, fn: (s: { tenantId: string }) => unknown) =>
    fn({ tenantId: "t-1" }),
}));
vi.mock("@/lib/prisma", () => ({
  prisma: {
    instance: { findFirst: vi.fn(), update: vi.fn(), updateMany: vi.fn() },
  },
}));
vi.mock("@/lib/providers", () => ({ resolveProvider: vi.fn() }));
vi.mock("@/lib/queue", () => ({
  enqueueProvision: vi.fn(),
  countProvisionWorkers: vi.fn(),
}));
vi.mock("@/lib/logger", () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { prisma } from "@/lib/prisma";
import { resolveProvider } from "@/lib/providers";
import { enqueueProvision, countProvisionWorkers } from "@/lib/queue";
import { logger } from "@/lib/logger";
import { POST } from "../route";

const findFirst = prisma.instance.findFirst as ReturnType<typeof vi.fn>;
const update = prisma.instance.update as ReturnType<typeof vi.fn>;
const updateMany = prisma.instance.updateMany as ReturnType<typeof vi.fn>;
const enqueue = enqueueProvision as ReturnType<typeof vi.fn>;
const workerCount = countProvisionWorkers as ReturnType<typeof vi.fn>;
const mockResolve = resolveProvider as ReturnType<typeof vi.fn>;

// Status writes that were observed via guarded updateMany calls.
function guardedStatuses() {
  return updateMany.mock.calls.map((c) => c[0].data.status);
}

// Stateful fake (#236): simulates the DB row so guarded transitions behave
// like real updateMany — a write only lands when the row's current status
// is in the `where.status.in` set. Lets race tests assert convergence.
function statefulRow(initial: Record<string, unknown>) {
  const row = { ...instance(initial) };
  findFirst.mockImplementation(async () => ({ ...row }));
  updateMany.mockImplementation(
    async (args: { where: { status?: { in: string[] } }; data: { status: string } }) => {
      const allowed = args.where.status?.in;
      if (allowed && !allowed.includes(row.status as string)) return { count: 0 };
      row.status = args.data.status;
      return { count: 1 };
    }
  );
  return row;
}

function instance(over: Record<string, unknown> = {}) {
  return {
    id: "inst-1",
    tenantId: "t-1",
    status: "STOPPED",
    vmRefId: "vm-alice-001",
    computePoolId: "pool-1",
    computePool: { type: "vsphere" },
    ...over,
  };
}

function post(action: string) {
  const req = new NextRequest(`http://test/api/instances/inst-1?action=${action}`, {
    method: "POST",
  });
  return POST(req, { params: Promise.resolve({ id: "inst-1" }) });
}

async function settle() {
  // Let the handler's background IIFE run to completion.
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

beforeEach(() => {
  vi.clearAllMocks();
  update.mockResolvedValue({});
  updateMany.mockResolvedValue({ count: 1 });
  enqueue.mockResolvedValue(undefined);
  // Default: a worker is online → no "stuck at PENDING" warning.
  workerCount.mockResolvedValue(1);
});

describe("POST ?action=start|restart (#233 — must NOT re-provision an existing VM)", () => {
  it("start with an existing VM powers it on — never enqueues provision", async () => {
    findFirst.mockResolvedValue(instance());
    const start = vi.fn().mockResolvedValue(undefined);
    const stop = vi.fn();
    mockResolve.mockReturnValue({ start, stop });

    const res = await post("start");
    await settle();

    expect(enqueue).not.toHaveBeenCalled(); // the #233 bug was exactly this call
    expect(start).toHaveBeenCalledWith("inst-1", "vm-alice-001");
    expect(stop).not.toHaveBeenCalled();
    const body = await res.json();
    expect(body.ok).toBe(true);
    // Final state lands on RUNNING.
    expect(guardedStatuses()).toContain("RUNNING");
  });

  it("restart with an existing VM = stop then start — never enqueues provision", async () => {
    findFirst.mockResolvedValue(instance({ status: "RUNNING" }));
    const calls: string[] = [];
    const start = vi.fn().mockImplementation(async () => calls.push("start"));
    const stop = vi.fn().mockImplementation(async () => calls.push("stop"));
    mockResolve.mockReturnValue({ start, stop });

    await post("restart");
    await settle();

    expect(enqueue).not.toHaveBeenCalled();
    expect(calls).toEqual(["stop", "start"]);
  });

  it("start with NO vmRefId falls back to provisioning (VM never existed)", async () => {
    findFirst.mockResolvedValue(instance({ vmRefId: null }));
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });

    const res = await post("start");

    expect(enqueue).toHaveBeenCalledWith(
      expect.objectContaining({ instanceId: "inst-1", tenantId: "t-1" })
    );
    const body = await res.json();
    expect(body.status).toBe("PENDING");
    expect(body.warning).toBeUndefined();
  });

  it("power-on failure is logged and the instance is NOT marked RUNNING", async () => {
    findFirst.mockResolvedValue(instance());
    const start = vi.fn().mockRejectedValue(new Error("powerOn failed"));
    mockResolve.mockReturnValue({ start, stop: vi.fn() });

    await post("start");
    await settle();

    expect(logger.error).toHaveBeenCalled();
    expect(guardedStatuses()).not.toContain("RUNNING");
    // Failure surfaces as ERROR (guarded) so the UI offers recovery (#236).
    expect(guardedStatuses()).toContain("ERROR");
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("stop action unchanged: stops and marks STOPPED", async () => {
    findFirst.mockResolvedValue(instance({ status: "RUNNING" }));
    const stop = vi.fn().mockResolvedValue(undefined);
    mockResolve.mockReturnValue({ start: vi.fn(), stop });

    const res = await post("stop");
    await settle();

    expect(stop).toHaveBeenCalledWith("inst-1", "vm-alice-001");
    const body = await res.json();
    expect(body.status).toBe("STOPPING");
    expect(guardedStatuses()).toContain("STOPPED");
  });

  it("start on an already-RUNNING instance is an idempotent no-op (review MEDIUM-1)", async () => {
    const row = statefulRow({ status: "RUNNING" });
    const start = vi.fn();
    mockResolve.mockReturnValue({ start, stop: vi.fn() });

    const res = await post("start");
    await settle();

    expect(start).not.toHaveBeenCalled();
    expect(enqueue).not.toHaveBeenCalled();
    expect(row.status).toBe("RUNNING"); // no state write landed
    const body = await res.json();
    expect(body.status).toBe("RUNNING");
  });

  it("lifecycle actions on a DELETED instance are 409, never resurrect (review LOW-1)", async () => {
    findFirst.mockResolvedValue(instance({ status: "DELETED" }));
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });

    for (const action of ["start", "restart", "stop"]) {
      const res = await post(action);
      expect(res.status).toBe(409);
    }
    await settle();
    expect(update).not.toHaveBeenCalled();
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("unknown action is a 400", async () => {
    findFirst.mockResolvedValue(instance());
    const res = await post("explode");
    expect(res.status).toBe(400);
  });
});

describe("#236 — lifecycle transitions are guarded, concurrent ops converge", () => {
  it("rapid stop→start converges to STOPPED: the start is rejected with 409", async () => {
    const row = statefulRow({ status: "RUNNING" });
    const stop = vi.fn(async () => {});
    const start = vi.fn(async () => {});
    mockResolve.mockReturnValue({ start, stop });

    // Both requests land before either background IIFE completes.
    const [stopRes, startRes] = await Promise.all([post("stop"), post("start")]);
    await settle();

    expect(stopRes.status).toBe(200);
    expect(startRes.status).toBe(409); // row is STOPPING — start cannot steal it
    expect(start).not.toHaveBeenCalled();
    expect(row.status).toBe("STOPPED"); // deterministic final state
  });

  it("start completion cannot clobber a stop that overtook it (no last-writer-wins)", async () => {
    const row = statefulRow({ status: "STOPPED" });
    let releaseStart: () => void = () => {};
    const start = vi.fn(() => new Promise<void>((r) => (releaseStart = r)));
    const stop = vi.fn(async () => {});
    mockResolve.mockReturnValue({ start, stop });

    await post("start"); // row → INITIALIZING, provider.start in flight
    await settle();
    await post("stop"); // user changes their mind: row → STOPPING
    await settle();
    releaseStart(); // provider.start finally resolves
    await settle();

    // The start IIFE's RUNNING write must not land — stop owns the row now.
    expect(row.status).toBe("STOPPED");
  });

  it("stop while another op owns the row is 409", async () => {
    statefulRow({ status: "STOPPING" });
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });
    const res = await post("stop");
    expect(res.status).toBe(409);
  });

  it("ERROR instance with a VM can be restarted (UI recovery path)", async () => {
    const row = statefulRow({ status: "ERROR" });
    const calls: string[] = [];
    const start = vi.fn(async () => calls.push("start"));
    const stop = vi.fn(async () => calls.push("stop"));
    mockResolve.mockReturnValue({ start, stop });

    const res = await post("restart");
    await settle();

    expect(res.status).toBe(200);
    expect(calls).toEqual(["stop", "start"]);
    expect(enqueue).not.toHaveBeenCalled(); // never re-provision (#233)
    expect(row.status).toBe("RUNNING");
  });

  it("ERROR instance with a VM can be started", async () => {
    const row = statefulRow({ status: "ERROR" });
    mockResolve.mockReturnValue({ start: vi.fn(async () => {}), stop: vi.fn() });
    const res = await post("start");
    await settle();
    expect(res.status).toBe(200);
    expect(row.status).toBe("RUNNING");
  });

  it("stop failure surfaces as ERROR, not a stuck STOPPING row", async () => {
    const row = statefulRow({ status: "RUNNING" });
    const stop = vi.fn().mockRejectedValue(new Error("powerOff failed"));
    mockResolve.mockReturnValue({ start: vi.fn(), stop });

    await post("stop");
    await settle();

    expect(logger.error).toHaveBeenCalled();
    expect(row.status).toBe("ERROR");
  });

  it("#249: RUNNING row with no vmRefId gets a distinct corrupt-state message, not 'retry later'", async () => {
    statefulRow({ status: "RUNNING", vmRefId: null });
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });
    const res = await post("restart");
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toMatch(/inconsistent/i);
    expect(body.error).not.toMatch(/Another lifecycle operation/);
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("start with NO vmRefId does not enqueue a duplicate provision while one is pending", async () => {
    statefulRow({ status: "PENDING", vmRefId: null });
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });
    const res = await post("start");
    expect(res.status).toBe(409);
    expect(enqueue).not.toHaveBeenCalled();
  });
});

describe("re-provision path uses enqueueProvision (timeout-bounded), not a bare queue add", () => {
  it("enqueue failure marks the claimed PENDING row ERROR with a message — no silent orphan", async () => {
    const row = statefulRow({ status: "STOPPED", vmRefId: null });
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });
    enqueue.mockRejectedValue(new Error("制备任务入队超时：队列服务（Redis）不可达。"));

    const res = await post("start");
    await settle();

    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.error).toMatch(/Redis/);
    expect(row.status).toBe("ERROR");
    const errWrite = updateMany.mock.calls.find((c) => c[0].data.status === "ERROR");
    expect(errWrite?.[0].data.errorMessage).toMatch(/入队失败/);
  });

  it("warns when zero provision workers are online (#259 parity with deploy)", async () => {
    findFirst.mockResolvedValue(instance({ vmRefId: null }));
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });
    workerCount.mockResolvedValue(0);

    const res = await post("start");

    const body = await res.json();
    expect(body.status).toBe("PENDING");
    expect(body.warning).toMatch(/worker/);
  });

  it("stays quiet when the worker count is indeterminate (null)", async () => {
    findFirst.mockResolvedValue(instance({ vmRefId: null }));
    mockResolve.mockReturnValue({ start: vi.fn(), stop: vi.fn() });
    workerCount.mockResolvedValue(null);

    const res = await post("start");

    const body = await res.json();
    expect(body.status).toBe("PENDING");
    expect(body.warning).toBeUndefined();
  });
});
