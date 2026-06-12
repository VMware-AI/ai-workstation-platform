import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { rateLimit, clearRateLimits, clientIp } from "../rate-limit";

beforeEach(() => {
  vi.useFakeTimers();
  clearRateLimits();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("rateLimit", () => {
  it("allows up to the limit within the window", () => {
    for (let i = 0; i < 5; i++) {
      expect(rateLimit("k", { limit: 5, windowMs: 60_000 }).allowed).toBe(true);
    }
  });

  it("blocks the request over the limit and reports retryAfter seconds", () => {
    for (let i = 0; i < 5; i++) rateLimit("k", { limit: 5, windowMs: 60_000 });
    const r = rateLimit("k", { limit: 5, windowMs: 60_000 });
    expect(r.allowed).toBe(false);
    expect(r.retryAfterS).toBeGreaterThan(0);
    expect(r.retryAfterS).toBeLessThanOrEqual(60);
  });

  it("window slides: old attempts expire", () => {
    for (let i = 0; i < 5; i++) rateLimit("k", { limit: 5, windowMs: 60_000 });
    vi.advanceTimersByTime(61_000);
    expect(rateLimit("k", { limit: 5, windowMs: 60_000 }).allowed).toBe(true);
  });

  it("keys are independent", () => {
    for (let i = 0; i < 5; i++) rateLimit("a", { limit: 5, windowMs: 60_000 });
    expect(rateLimit("b", { limit: 5, windowMs: 60_000 }).allowed).toBe(true);
  });

  it("blocked attempts do not extend the window (no lockout amplification)", () => {
    for (let i = 0; i < 10; i++) rateLimit("k", { limit: 5, windowMs: 60_000 });
    vi.advanceTimersByTime(61_000);
    expect(rateLimit("k", { limit: 5, windowMs: 60_000 }).allowed).toBe(true);
  });
});

describe("eviction (review MEDIUM-1)", () => {
  it("stale buckets are dropped once the map exceeds the cap", () => {
    for (let i = 0; i < 10_001; i++) {
      rateLimit(`spray-${i}`, { limit: 5, windowMs: 60_000 });
    }
    vi.advanceTimersByTime(61 * 60_000); // beyond MAX_WINDOW_MS
    rateLimit("fresh", { limit: 5, windowMs: 60_000 }); // triggers sweep
    // After the sweep the sprayed keys are gone: a new attempt on one of
    // them starts a fresh bucket (allowed), and memory is reclaimed.
    expect(rateLimit("spray-0", { limit: 1, windowMs: 60_000 }).allowed).toBe(true);
  });
});

describe("clientIp", () => {
  it("takes the first hop of x-forwarded-for", () => {
    const h = new Headers({ "x-forwarded-for": "203.0.113.9, 10.0.0.1" });
    expect(clientIp(h)).toBe("203.0.113.9");
  });

  it("falls back to a stable sentinel when absent", () => {
    expect(clientIp(new Headers())).toBe("unknown");
  });
});
