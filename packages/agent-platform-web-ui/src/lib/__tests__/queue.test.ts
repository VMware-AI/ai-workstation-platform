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
  Worker: class Worker {},
  Job: class Job {},
}));

import { countProvisionWorkers } from "../queue";

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
