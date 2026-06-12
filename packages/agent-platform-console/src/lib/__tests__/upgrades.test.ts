// Contract tests for the blue-green upgrades API client.
//
// The critical invariant: destructive mutations (cutover/rollback/cleanup)
// must NEVER fall back to mock fixtures — a backend failure (409/403/500/
// network) has to propagate so the confirm dialog shows it instead of
// rendering a fake successful state transition. Read paths (list/get) only
// fall back to fixtures when the route itself is missing (404).

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { mockUpgrades, upgradesApi } from "../api/upgrades";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

function fetchResponding(status: number, body: unknown = {}): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: `HTTP ${status}`,
    json: async () => body,
  }) as unknown as typeof fetch;
}

function fetchNetworkError(): typeof fetch {
  return vi
    .fn()
    .mockRejectedValue(new TypeError("Failed to fetch")) as unknown as typeof fetch;
}

describe("upgradesApi destructive mutations", () => {
  const cases = [
    ["cutover", () => upgradesApi.cutover("upg-0001", true)],
    ["rollback", () => upgradesApi.rollback("upg-0001", true)],
    ["cleanup", () => upgradesApi.cleanup("upg-0001", true, false)],
  ] as const;

  it.each(cases)("%s rejects with ApiError on backend 409", async (_name, call) => {
    global.fetch = fetchResponding(409, { detail: "invalid state transition" });
    await expect(call()).rejects.toBeInstanceOf(ApiError);
    await expect(call()).rejects.toMatchObject({
      status: 409,
      message: expect.stringContaining("invalid state transition"),
    });
  });

  it.each(cases)("%s rejects on backend 500 (no mock fallback)", async (_name, call) => {
    global.fetch = fetchResponding(500, { detail: "boom" });
    await expect(call()).rejects.toMatchObject({ status: 500 });
  });

  it.each(cases)("%s rejects on network error (no mock fallback)", async (_name, call) => {
    global.fetch = fetchNetworkError();
    await expect(call()).rejects.toBeInstanceOf(TypeError);
  });

  it.each(cases)("%s never resolves to a _mock object on failure", async (_name, call) => {
    global.fetch = fetchResponding(503, { detail: "unavailable" });
    let resolved: unknown = null;
    try {
      resolved = await call();
    } catch {
      // expected — failure must propagate
    }
    expect(resolved).toBeNull();
  });

  it("cutover resolves with the backend payload on success", async () => {
    const backend = { ...mockUpgrades()[0], state: "cutover_in_progress" };
    global.fetch = fetchResponding(200, backend);
    await expect(upgradesApi.cutover("upg-0001", true)).resolves.toEqual(backend);
  });
});

describe("upgradesApi start (effectful mutation)", () => {
  const planReq = {
    tenant_id: "tenant-a",
    from_version: "v1",
    to_version: "v2",
    vms: [
      { owner_id: "alice", green_vm_id: "vm-1", blue_intended_name: "blue-1" },
    ],
  };

  it("falls back to mock only on 404 (route not mounted)", async () => {
    global.fetch = fetchResponding(404, { detail: "Not Found" });
    const res = await upgradesApi.start(planReq);
    expect(res._mock).toBe(true);
  });

  it("propagates 409 instead of faking a started upgrade", async () => {
    global.fetch = fetchResponding(409, { detail: "already running" });
    await expect(upgradesApi.start(planReq)).rejects.toMatchObject({
      status: 409,
    });
  });

  it("propagates network errors instead of faking a started upgrade", async () => {
    global.fetch = fetchNetworkError();
    await expect(upgradesApi.start(planReq)).rejects.toBeInstanceOf(TypeError);
  });
});

describe("upgradesApi read paths", () => {
  it("list falls back to mock fixtures only on 404 (route not mounted)", async () => {
    global.fetch = fetchResponding(404, { detail: "Not Found" });
    const res = await upgradesApi.list();
    expect(res._mock).toBe(true);
    expect(res.upgrades.length).toBeGreaterThan(0);
  });

  it("list propagates 500 instead of falling back", async () => {
    global.fetch = fetchResponding(500, { detail: "boom" });
    await expect(upgradesApi.list()).rejects.toMatchObject({ status: 500 });
  });

  it("list propagates network errors instead of falling back", async () => {
    global.fetch = fetchNetworkError();
    await expect(upgradesApi.list()).rejects.toBeInstanceOf(TypeError);
  });

  it("get falls back to the matching fixture only on 404", async () => {
    global.fetch = fetchResponding(404, { detail: "Not Found" });
    const res = await upgradesApi.get("upg-0001");
    expect(res._mock).toBe(true);
    expect(res.id).toBe("upg-0001");
  });

  it("get propagates 503 instead of falling back", async () => {
    global.fetch = fetchResponding(503, { detail: "unavailable" });
    await expect(upgradesApi.get("upg-0001")).rejects.toMatchObject({ status: 503 });
  });
});
