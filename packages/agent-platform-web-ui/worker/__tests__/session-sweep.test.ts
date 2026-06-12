import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/prisma", () => ({
  prisma: {
    session: { deleteMany: vi.fn() },
  },
}));

import { prisma } from "@/lib/prisma";
import { SESSION_IDLE_MS } from "@/lib/session-config";
import { runSessionSweep } from "../jobs/session-sweep";

const deleteMany = prisma.session.deleteMany as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  deleteMany.mockResolvedValue({ count: 0 });
});

describe("runSessionSweep (#239)", () => {
  it("deletes sessions past their absolute expiry OR idle window", async () => {
    await runSessionSweep();

    expect(deleteMany).toHaveBeenCalledTimes(1);
    const where = deleteMany.mock.calls[0][0].where;
    expect(where.OR).toHaveLength(2);
    const [expired, idle] = where.OR;
    expect(expired.expiresAt.lt).toBeInstanceOf(Date);
    expect(idle.lastSeenAt.lt).toBeInstanceOf(Date);
    // The idle cutoff mirrors the live check in auth.ts — same constant.
    const driftMs = Math.abs(
      Date.now() - SESSION_IDLE_MS - idle.lastSeenAt.lt.getTime()
    );
    expect(driftMs).toBeLessThan(5_000);
  });

  it("a sweep failure is swallowed (logged), never thrown to the scheduler", async () => {
    deleteMany.mockRejectedValue(new Error("db down"));
    await expect(runSessionSweep()).resolves.toBeUndefined();
  });
});
