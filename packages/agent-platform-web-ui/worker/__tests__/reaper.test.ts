import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the prisma client and provider registry before importing the reaper.
vi.mock("@/lib/prisma", () => ({
  prisma: {
    instance: { findMany: vi.fn(), update: vi.fn(), updateMany: vi.fn() },
    provisionEvent: { create: vi.fn() },
  },
}));
vi.mock("@/lib/providers", () => ({
  resolveProvider: vi.fn(),
}));

import { prisma } from "@/lib/prisma";
import { resolveProvider } from "@/lib/providers";
import { runReaper } from "../jobs/reaper";

const findMany = prisma.instance.findMany as ReturnType<typeof vi.fn>;
const update = prisma.instance.update as ReturnType<typeof vi.fn>;
const updateMany = prisma.instance.updateMany as ReturnType<typeof vi.fn>;
const createEvent = prisma.provisionEvent.create as ReturnType<typeof vi.fn>;
const mockResolve = resolveProvider as ReturnType<typeof vi.fn>;

function staleInstance(over: Record<string, unknown> = {}) {
  return {
    id: "inst-1",
    name: "vm-alice-001",
    status: "PROVISIONING",
    vmRefId: null, // the dominant real case: vmRefId is only written at RUNNING
    startedAt: null,
    computePool: { type: "vsphere" },
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  update.mockResolvedValue({});
  updateMany.mockResolvedValue({ count: 1 }); // guarded write lands by default
  createEvent.mockResolvedValue({});
});

// The reaper's ERROR write is a guarded transition (#244).
function lastGuardedWrite() {
  const call = updateMany.mock.calls.at(-1)?.[0];
  return { where: call?.where, data: call?.data };
}

describe("runReaper", () => {
  it("queries exactly the timed-out lifecycle states (#236 review M-1/M-2: PENDING + STOPPING)", async () => {
    findMany.mockResolvedValue([]);
    await runReaper();
    const where = findMany.mock.calls[0][0].where;
    expect(where.status).toEqual({ in: ["PENDING", "PROVISIONING", "INITIALIZING", "STOPPING"] });
    expect(where.updatedAt.lt).toBeInstanceOf(Date);
    expect(update).not.toHaveBeenCalled();
    expect(createEvent).not.toHaveBeenCalled();
  });

  it("destroys a fresh stuck provision by NAME (vmRefId is null until RUNNING) — #90 M23", async () => {
    findMany.mockResolvedValue([staleInstance()]);
    const destroy = vi.fn().mockResolvedValue(undefined);
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    // The provider clones the VM under the instance name; that's the handle.
    expect(destroy).toHaveBeenCalledWith("inst-1", "vm-alice-001");
    // Destroy runs BEFORE the row is marked ERROR.
    expect(destroy.mock.invocationCallOrder[0]).toBeLessThan(updateMany.mock.invocationCallOrder[0]);
    const w = lastGuardedWrite();
    expect(w.where).toMatchObject({ id: "inst-1", status: { in: ["PROVISIONING"] } });
    expect(w.data).toMatchObject({ status: "ERROR" });
    expect(createEvent.mock.calls[0][0].data.message).toContain("destroyed");
  });

  it("prefers vmRefId over name when present", async () => {
    findMany.mockResolvedValue([staleInstance({ vmRefId: "vm-42" })]);
    const destroy = vi.fn().mockResolvedValue(undefined);
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    expect(destroy).toHaveBeenCalledWith("inst-1", "vm-42");
  });

  it("NEVER destroys an instance that once reached RUNNING (restart path) — review HIGH-2", async () => {
    findMany.mockResolvedValue([
      staleInstance({ vmRefId: "vm-42", startedAt: new Date("2026-06-01") }),
    ]);
    const destroy = vi.fn();
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    expect(destroy).not.toHaveBeenCalled();
    expect(lastGuardedWrite().data).toMatchObject({ status: "ERROR" });
    const eventMsg = createEvent.mock.calls[0][0].data.message;
    expect(eventMsg).toContain("NOT destroying");
    expect(eventMsg).toContain("manually");
  });

  it("still marks ERROR when destroy fails, and flags the orphan", async () => {
    findMany.mockResolvedValue([staleInstance()]);
    const destroy = vi.fn().mockRejectedValue(new Error("vCenter unreachable"));
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    expect(lastGuardedWrite().data).toMatchObject({ status: "ERROR" });
    const eventMsg = createEvent.mock.calls[0][0].data.message;
    expect(eventMsg).toContain("vCenter unreachable");
    expect(eventMsg.toLowerCase()).toContain("orphan");
  });

  it("handles pools with no provider without throwing", async () => {
    findMany.mockResolvedValue([staleInstance({ computePool: { type: "docker" } })]);
    mockResolve.mockReturnValue(null);

    await runReaper();

    expect(lastGuardedWrite().data).toMatchObject({ status: "ERROR" });
  });

  it("stale PENDING (lost provision job) is freed to ERROR without destroying anything", async () => {
    // The guarded re-provision path (#236) only re-opens from STOPPED/ERROR —
    // a PENDING row whose queue job was lost must not dead-end at 409 forever.
    findMany.mockResolvedValue([staleInstance({ status: "PENDING" })]);
    const destroy = vi.fn();
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    // The provision worker never picked the job up, so no VM was cloned.
    expect(destroy).not.toHaveBeenCalled();
    const w = lastGuardedWrite();
    expect(w.where).toMatchObject({ status: { in: ["PENDING"] } });
    expect(w.data).toMatchObject({ status: "ERROR" });
    expect(createEvent.mock.calls[0][0].data.message).toContain("no VM");
  });

  it("stale STOPPING (server died mid-stop) is freed to ERROR, VM untouched", async () => {
    // Stop IIFEs die with the server process; the claimed row must not be a
    // permanent 409 dead-end (#236 review M-2). startedAt is always set here
    // (the instance was RUNNING), so the no-destroy branch applies.
    findMany.mockResolvedValue([
      staleInstance({ status: "STOPPING", vmRefId: "vm-42", startedAt: new Date("2026-06-01") }),
    ]);
    const destroy = vi.fn();
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    expect(destroy).not.toHaveBeenCalled();
    expect(lastGuardedWrite().data).toMatchObject({ status: "ERROR" });
  });

  it("one instance's destroy failure does not stop the rest of the sweep", async () => {
    findMany.mockResolvedValue([
      staleInstance({ id: "inst-1" }),
      staleInstance({ id: "inst-2", name: "vm-bob-002" }),
    ]);
    const destroy = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(undefined);
    mockResolve.mockReturnValue({ destroy });

    await runReaper();

    expect(destroy).toHaveBeenCalledTimes(2);
    expect(updateMany).toHaveBeenCalledTimes(2);
  });

  it("#244: row that moved on during the sweep is left alone — no ERROR clobber, no lying event", async () => {
    // Stop completed (row → STOPPED) while the reaper was mid-sweep: the
    // guarded write returns count 0 and the timeout event must not be logged.
    findMany.mockResolvedValue([
      staleInstance({ status: "STOPPING", vmRefId: "vm-42", startedAt: new Date("2026-06-01") }),
    ]);
    updateMany.mockResolvedValue({ count: 0 });
    mockResolve.mockReturnValue({ destroy: vi.fn() });

    await runReaper();

    expect(createEvent).not.toHaveBeenCalled();
  });
});
