import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("parses successful JSON responses", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok" }),
    }) as unknown as typeof fetch;

    await expect(api.healthz()).resolves.toEqual({ status: "ok" });
  });

  it("throws ApiError with status code on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      statusText: "Forbidden",
      json: async () => ({ detail: "rbac denied" }),
    }) as unknown as typeof fetch;

    await expect(api.listVms()).rejects.toMatchObject({
      status: 403,
      message: expect.stringContaining("rbac denied"),
    });
    await expect(api.listVms()).rejects.toBeInstanceOf(ApiError);
  });

  it("encodes audit limit as query string", async () => {
    const spy = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ entries: [], limit: 50 }),
    });
    global.fetch = spy as unknown as typeof fetch;

    await api.listAudit(50);
    expect(spy).toHaveBeenCalledWith(
      expect.stringContaining("/admin/audit?limit=50"),
      expect.any(Object),
    );
  });
});
