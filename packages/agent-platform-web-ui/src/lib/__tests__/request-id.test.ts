import { describe, it, expect } from "vitest";
import { normalizeRequestId, resolveRequestId, REQUEST_ID_HEADER } from "../request-id";

describe("normalizeRequestId", () => {
  it("accepts a sane id", () => {
    expect(normalizeRequestId("req-abc_123.DEF")).toBe("req-abc_123.DEF");
  });

  it("rejects control characters", () => {
    expect(normalizeRequestId("a\nb")).toBeNull();
  });

  it("rejects overlong values", () => {
    expect(normalizeRequestId("a".repeat(129))).toBeNull();
  });

  it("rejects empty and null", () => {
    expect(normalizeRequestId("")).toBeNull();
    expect(normalizeRequestId(null)).toBeNull();
  });
});

describe("resolveRequestId", () => {
  it("keeps a sane incoming id", () => {
    expect(resolveRequestId("caller-42")).toBe("caller-42");
  });

  it("generates a sane id when incoming is missing", () => {
    const rid = resolveRequestId(null);
    expect(normalizeRequestId(rid)).toBe(rid);
  });

  it("replaces garbage with a generated id", () => {
    const rid = resolveRequestId("x".repeat(500));
    expect(rid).not.toBe("x".repeat(500));
    expect(normalizeRequestId(rid)).toBe(rid);
  });

  it("generates unique ids", () => {
    expect(resolveRequestId(null)).not.toBe(resolveRequestId(null));
  });
});

describe("REQUEST_ID_HEADER", () => {
  it("matches the cross-service header name", () => {
    expect(REQUEST_ID_HEADER.toLowerCase()).toBe("x-request-id");
  });
});
