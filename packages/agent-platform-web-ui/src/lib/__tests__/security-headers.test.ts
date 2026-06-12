import { describe, expect, it } from "vitest";
import { buildSecurityHeaders } from "../security-headers";

function headerMap(headers: { key: string; value: string }[]) {
  return new Map(headers.map((h) => [h.key, h.value]));
}

describe("buildSecurityHeaders", () => {
  it("returns the full baseline header set (issue #230 acceptance)", () => {
    const map = headerMap(buildSecurityHeaders("production"));
    expect(map.get("Strict-Transport-Security")).toBe(
      "max-age=31536000; includeSubDomains"
    );
    expect(map.get("X-Frame-Options")).toBe("DENY");
    expect(map.get("X-Content-Type-Options")).toBe("nosniff");
    expect(map.get("Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(map.get("Permissions-Policy")).toBe(
      "camera=(), microphone=(), geolocation=()"
    );
  });

  it("ships CSP as Report-Only first (enforce later, per #230)", () => {
    const map = headerMap(buildSecurityHeaders("production"));
    expect(map.has("Content-Security-Policy-Report-Only")).toBe(true);
    expect(map.has("Content-Security-Policy")).toBe(false);
  });

  it("CSP locks down framing, plugins, and base-uri", () => {
    const map = headerMap(buildSecurityHeaders("production"));
    const csp = map.get("Content-Security-Policy-Report-Only") ?? "";
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
  });

  it("production CSP does not allow eval", () => {
    const map = headerMap(buildSecurityHeaders("production"));
    const csp = map.get("Content-Security-Policy-Report-Only") ?? "";
    expect(csp).not.toContain("unsafe-eval");
  });

  it("development CSP allows eval and HMR websockets", () => {
    const map = headerMap(buildSecurityHeaders("development"));
    const csp = map.get("Content-Security-Policy-Report-Only") ?? "";
    expect(csp).toContain("'unsafe-eval'");
    expect(csp).toContain("ws:");
  });

  it("CSP is a single-line header value (no newlines or double spaces)", () => {
    const map = headerMap(buildSecurityHeaders("production"));
    const csp = map.get("Content-Security-Policy-Report-Only") ?? "";
    expect(csp).not.toMatch(/\n/);
    expect(csp).not.toMatch(/\s{2,}/);
  });

  it("returns a new array per call (no shared mutable state)", () => {
    const a = buildSecurityHeaders("production");
    const b = buildSecurityHeaders("production");
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });
});
