import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchJson } from "@/lib/fetchJson";

function mockFetch(impl: () => Promise<Response> | Response) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchJson", () => {
  it("returns typed data on 2xx", async () => {
    mockFetch(() => jsonResponse({ items: [1, 2] }, 200));
    const r = await fetchJson<{ items: number[] }>("/api/x");
    expect(r).toEqual({ ok: true, data: { items: [1, 2] } });
  });

  it("surfaces the server {error} string on a non-2xx", async () => {
    mockFetch(() => jsonResponse({ error: "计算池仍有运行中的实例" }, 409));
    const r = await fetchJson("/api/x", { method: "DELETE" });
    expect(r).toEqual({ ok: false, error: "计算池仍有运行中的实例", status: 409 });
  });

  it("extracts zod formErrors[0] when error is an object", async () => {
    mockFetch(() => jsonResponse({ error: { formErrors: ["名称必填"] } }, 400));
    const r = await fetchJson("/api/x", { method: "POST" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("名称必填");
  });

  it("falls back to a status-code message when body has no usable error", async () => {
    mockFetch(() => jsonResponse(null, 500));
    const r = await fetchJson("/api/x");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toMatch(/HTTP 500/);
      expect(r.status).toBe(500);
    }
  });

  it("does not throw when the error body is not JSON", async () => {
    mockFetch(
      () =>
        ({
          ok: false,
          status: 502,
          json: async () => {
            throw new SyntaxError("Unexpected token");
          },
        }) as unknown as Response
    );
    const r = await fetchJson("/api/x");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.status).toBe(502);
  });

  it("returns a network error (never throws) when fetch rejects", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    const r = await fetchJson("/api/x", { method: "POST" });
    expect(r).toEqual({ ok: false, error: "网络错误，请稍后重试", status: 0 });
  });
});
