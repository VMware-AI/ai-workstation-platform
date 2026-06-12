import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Guarded status writes in the provision worker (#244): a DELETE landing at
// any point must never be resurrected by a worker write.

vi.mock("@/lib/prisma", () => ({
  prisma: {
    instance: { findUnique: vi.fn(), updateMany: vi.fn(), update: vi.fn() },
    provisionEvent: { create: vi.fn() },
  },
}));
vi.mock("@/lib/providers", () => ({ resolveProvider: vi.fn() }));
vi.mock("@/lib/crypto", () => ({ decrypt: vi.fn((v: string) => v) }));
vi.mock("@/lib/queue", () => ({})); // module body constructs a Redis client
vi.mock("@/lib/logger", () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { prisma } from "@/lib/prisma";
import { resolveProvider } from "@/lib/providers";
import { processProvisionJob } from "../jobs/provision";

const findUnique = prisma.instance.findUnique as ReturnType<typeof vi.fn>;
const updateMany = prisma.instance.updateMany as ReturnType<typeof vi.fn>;
const update = prisma.instance.update as ReturnType<typeof vi.fn>;
const createEvent = prisma.provisionEvent.create as ReturnType<typeof vi.fn>;
const mockResolve = resolveProvider as ReturnType<typeof vi.fn>;

function row(over: Record<string, unknown> = {}) {
  return {
    id: "inst-1",
    tenantId: "t-1",
    name: "vm-alice-001",
    status: "PENDING",
    deployParams: "enc",
    computePool: { type: "vsphere" },
    ...over,
  };
}

// Stateful fake: updateMany honors the status guard against a live row.
function statefulRow(initial: Record<string, unknown>) {
  const state = { provisionerJobId: null as string | null, ...row(initial) };
  findUnique.mockImplementation(async () => ({ ...state }));
  updateMany.mockImplementation(
    async (args: {
      where: { status?: { in: string[] }; provisionerJobId?: string };
      data: { status: string; provisionerJobId?: string };
    }) => {
      const allowed = args.where.status?.in;
      if (allowed && !allowed.includes(state.status as string)) return { count: 0 };
      // Ownership fence (PR #253 M-2): writes scoped to a job id miss when
      // another job has since claimed the row.
      if (
        args.where.provisionerJobId !== undefined &&
        args.where.provisionerJobId !== state.provisionerJobId
      ) {
        return { count: 0 };
      }
      state.status = args.data.status;
      if (args.data.provisionerJobId !== undefined) state.provisionerJobId = args.data.provisionerJobId;
      return { count: 1 };
    }
  );
  return state;
}

function job() {
  return {
    id: "job-1",
    data: { instanceId: "inst-1", tenantId: "t-1", computePoolId: undefined },
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  update.mockResolvedValue({});
  updateMany.mockResolvedValue({ count: 1 });
  createEvent.mockResolvedValue({});
});

describe("provision worker guarded writes (#244)", () => {
  it("a DELETED row is never claimed — no resurrection, no provider call", async () => {
    statefulRow({ status: "DELETED" });
    const provision = vi.fn();
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    expect(provision).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled(); // no plain writes at all
  });

  it("DELETE landing mid-provision: the RUNNING completion write is dropped", async () => {
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockImplementation(async () => {
      // DELETE wins the race while the provider is cloning.
      state.status = "DELETED";
      return { vmRefId: "vm-9", ipAddress: "10.0.0.9", endpoint: null, containerId: null };
    });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    expect(state.status).toBe("DELETED"); // not resurrected to RUNNING
  });

  it("DELETE landing mid-provision: the ERROR failure write is dropped too", async () => {
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockImplementation(async () => {
      state.status = "DELETED";
      throw new Error("clone failed");
    });
    mockResolve.mockReturnValue({ provision });

    await expect(processProvisionJob(job())).rejects.toThrow("clone failed");
    expect(state.status).toBe("DELETED"); // not flipped to ERROR
    // No false audit record: the ERROR event is only written when the
    // guarded transition landed.
    const events = createEvent.mock.calls.map((c) => c[0].data.event);
    expect(events).not.toContain("ERROR");
  });

  it("DELETE landing mid-provision (success path): orphan warning lands in the audit trail", async () => {
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockImplementation(async () => {
      state.status = "DELETED";
      return { vmRefId: "vm-9", ipAddress: null, endpoint: null, containerId: null };
    });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    const events = createEvent.mock.calls.map((c) => c[0].data.event);
    expect(events).toContain("ORPHAN_WARNING");
    expect(events).not.toContain("RUNNING");
  });

  it("happy path still works: PENDING → PROVISIONING → RUNNING via guarded writes", async () => {
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockResolvedValue({
      vmRefId: "vm-9",
      ipAddress: "10.0.0.9",
      endpoint: "http://10.0.0.9",
      containerId: null,
    });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    expect(state.status).toBe("RUNNING");
    expect(update).not.toHaveBeenCalled(); // every status write is guarded
  });

  it("BullMQ retry of a crashed attempt re-claims from PROVISIONING", async () => {
    const state = statefulRow({ status: "PROVISIONING" });
    const provision = vi.fn().mockResolvedValue({
      vmRefId: "vm-9",
      ipAddress: null,
      endpoint: null,
      containerId: null,
    });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    expect(provision).toHaveBeenCalled();
    expect(state.status).toBe("RUNNING");
  });
});

describe("bootstrap token (#231 — agent self-registration chain)", () => {
  const URL = "https://cp.example.invalid";

  beforeEach(() => {
    process.env.CONTROL_PLANE_URL = URL;
  });
  afterEach(() => {
    delete process.env.CONTROL_PLANE_URL;
  });

  it("claim stores a sha256 token hash + expiry, and the plaintext goes to the provider", async () => {
    statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockResolvedValue({ vmRefId: "vm-9" });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    const claim = updateMany.mock.calls[0][0];
    expect(claim.data.agentTokenHash).toMatch(/^[0-9a-f]{64}$/);
    expect(claim.data.agentTokenExpiresAt).toBeInstanceOf(Date);

    const input = provision.mock.calls[0][0];
    expect(input.bootstrap).toBeDefined();
    expect(input.bootstrap.controlPlaneUrl).toBe(URL);
    // The stored hash is the sha256 of the plaintext handed to the provider.
    const crypto = await import("crypto");
    const expected = crypto.createHash("sha256").update(input.bootstrap.token).digest("hex");
    expect(claim.data.agentTokenHash).toBe(expected);
  });

  it("no CONTROL_PLANE_URL → no token stored, no bootstrap passed (chain off, provisioning unaffected)", async () => {
    delete process.env.CONTROL_PLANE_URL;
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockResolvedValue({ vmRefId: "vm-9" });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    const claim = updateMany.mock.calls[0][0];
    expect(claim.data.agentTokenHash).toBeUndefined();
    expect(provision.mock.calls[0][0].bootstrap).toBeUndefined();
    expect(state.status).toBe("RUNNING");
  });

  it("VM registers first (row already RUNNING): completion still lands vmRefId, NO orphan warning", async () => {
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockImplementation(async () => {
      // cloud-init register beat the worker: row flipped to RUNNING.
      state.status = "RUNNING";
      return { vmRefId: "vm-9", ipAddress: "10.0.0.9", endpoint: null, containerId: null };
    });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job());

    expect(state.status).toBe("RUNNING");
    const events = createEvent.mock.calls.map((c) => c[0].data.event);
    expect(events).not.toContain("ORPHAN_WARNING");
    expect(events).toContain("RUNNING");
    // The completion write carried vmRefId (register couldn't know it).
    const completion = updateMany.mock.calls.at(-1)?.[0];
    expect(completion.data.vmRefId).toBe("vm-9");
  });
});

describe("stale-job fence (PR #253 review M-2)", () => {
  it("a stalled job's completion cannot clobber the row a newer job claimed", async () => {
    const state = statefulRow({ status: "PENDING" });
    const provision = vi.fn().mockImplementation(async () => {
      // While this (old) job's provider call runs, BullMQ requeued the work:
      // a NEW job claimed the row and finished.
      state.provisionerJobId = "job-NEW";
      state.status = "RUNNING";
      return { vmRefId: "vm-OLD", ipAddress: "10.0.0.1", endpoint: null, containerId: null };
    });
    mockResolve.mockReturnValue({ provision });

    await processProvisionJob(job()); // job-1 (the old job)

    // The old job's completion write must be dropped — vmRefId stays whatever
    // the new job wrote (the fake doesn't track it; the write simply missed).
    const completion = updateMany.mock.calls.at(-1)?.[0];
    expect(completion.where.provisionerJobId).toBe("job-1");
    const events = createEvent.mock.calls.map((c) => c[0].data.event);
    expect(events).toContain("ORPHAN_WARNING"); // old VM may be orphaned — flagged
  });
});
