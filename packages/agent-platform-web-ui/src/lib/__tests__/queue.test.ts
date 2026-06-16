import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the Redis client + BullMQ so importing queue.ts doesn't open a real
// connection. getWorkersCount is the only behavior under test here.
// vi.hoisted so the mock fn exists when the hoisted vi.mock factory runs
// (queue.ts constructs the Queue at module load).
const { getWorkersCount } = vi.hoisted(() => ({ getWorkersCount: vi.fn() }));
vi.mock("ioredis", () => ({ default: class IORedis {} }));
vi.mock("bullmq", () => ({
  Queue: class Queue {
    add = vi.fn();
    getWorkersCount = getWorkersCount;
  },
  // Capture constructor args so the worker options (concurrency) are assertable.
  Worker: class Worker {
    constructor(
      public name: string,
      public processor: unknown,
      public opts: { concurrency?: number }
    ) {}
  },
  Job: class Job {},
}));

import {
  countProvisionWorkers,
  createWorker,
  resolveProvisionConcurrency,
  DEFAULT_PROVISION_CONCURRENCY,
  MAX_PROVISION_CONCURRENCY,
} from "../queue";

describe("countProvisionWorkers (#259)", () => {
  beforeEach(() => getWorkersCount.mockReset());

  it("passes the worker count through", async () => {
    getWorkersCount.mockResolvedValue(3);
    expect(await countProvisionWorkers()).toBe(3);
  });

  it("reports a definitive zero (the case that triggers the deploy warning)", async () => {
    getWorkersCount.mockResolvedValue(0);
    expect(await countProvisionWorkers()).toBe(0);
  });

  // The null-on-failure contract (never block a succeeded deploy): when the
  // worker count can't be determined in time, return null so the caller skips
  // the warning. A thrown/rejected query hits the same `.catch(() => null)`.
  it("returns null when the query exceeds the timeout", async () => {
    // Resolves, but well after the timeout — the race must settle on null.
    getWorkersCount.mockImplementation(
      () => new Promise((r) => setTimeout(() => r(5), 100)),
    );
    expect(await countProvisionWorkers(20)).toBeNull();
  });
});

describe("resolveProvisionConcurrency (#257 M22)", () => {
  it("defaults when PROVISION_CONCURRENCY is unset", () => {
    expect(resolveProvisionConcurrency({})).toBe(DEFAULT_PROVISION_CONCURRENCY);
  });

  it("honours a valid positive integer override", () => {
    expect(resolveProvisionConcurrency({ PROVISION_CONCURRENCY: "20" })).toBe(20);
    expect(resolveProvisionConcurrency({ PROVISION_CONCURRENCY: "1" })).toBe(1);
  });

  it("falls back to the default on garbage / non-positive / non-integer", () => {
    for (const bad of ["0", "-3", "abc", "2.5", "", " "]) {
      expect(resolveProvisionConcurrency({ PROVISION_CONCURRENCY: bad })).toBe(
        DEFAULT_PROVISION_CONCURRENCY
      );
    }
  });

  it("clamps an over-large override to the ceiling (no silent vCenter flood)", () => {
    expect(resolveProvisionConcurrency({ PROVISION_CONCURRENCY: "500" })).toBe(
      MAX_PROVISION_CONCURRENCY
    );
    // the ceiling itself passes through unclamped
    expect(
      resolveProvisionConcurrency({
        PROVISION_CONCURRENCY: String(MAX_PROVISION_CONCURRENCY),
      })
    ).toBe(MAX_PROVISION_CONCURRENCY);
  });

  it("createWorker applies the resolved concurrency to the BullMQ Worker", () => {
    const prev = process.env.PROVISION_CONCURRENCY;
    try {
      process.env.PROVISION_CONCURRENCY = "7";
      const worker = createWorker(async () => {}) as unknown as { opts: { concurrency: number } };
      expect(worker.opts.concurrency).toBe(7);
    } finally {
      if (prev === undefined) delete process.env.PROVISION_CONCURRENCY;
      else process.env.PROVISION_CONCURRENCY = prev;
    }
  });
});
