import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  assertSafeHttpUrl,
  isBlockedIp,
  resolveSafeHttpUrl,
  UnsafeUrlError,
} from "@/lib/url-safe";

// First test file in the agent-platform-web-ui package (M40 from the codebase
// review). Targets the SSRF / DNS-rebinding guard introduced for H7
// (#93) and tightened by biao tang's pinned-lookup follow-up
// (2822491). Every parse / classification branch gets a test so a
// future loosening of the blocklist points at the rule that changed.

describe("isBlockedIp", () => {
  it.each([
    ["127.0.0.1", "loopback v4"],
    ["10.0.0.5", "RFC1918 10/8"],
    ["172.16.5.5", "RFC1918 172.16/12"],
    ["192.168.1.1", "RFC1918 192.168/16"],
    ["169.254.169.254", "AWS metadata link-local"],
    ["100.64.0.1", "CGNAT"],
    ["::1", "loopback v6"],
    ["fc00::1", "unique-local v6"],
    ["fe80::1", "link-local v6"],
    ["::ffff:10.0.0.1", "IPv4-mapped v6 of private addr"],
  ])("blocks %s (%s)", (ip) => {
    expect(isBlockedIp(ip)).toBe(true);
  });

  it.each([
    ["8.8.8.8", "Google DNS"],
    ["1.1.1.1", "Cloudflare DNS"],
    ["2001:4860:4860::8888", "Google DNS v6"],
  ])("permits %s (%s)", (ip) => {
    expect(isBlockedIp(ip)).toBe(false);
  });

  it("blocks anything that isn't a valid IP literal", () => {
    expect(isBlockedIp("not-an-ip")).toBe(true);
    expect(isBlockedIp("")).toBe(true);
  });
});

describe("resolveSafeHttpUrl — input validation", () => {
  it("rejects unparseable URLs", async () => {
    await expect(resolveSafeHttpUrl("not a url")).rejects.toThrow(UnsafeUrlError);
  });

  it("rejects http:// by default", async () => {
    await expect(resolveSafeHttpUrl("http://api.openai.com/v1")).rejects.toThrow(
      /must use https/i,
    );
  });

  it("accepts http:// when allowHttp is set", async () => {
    const out = await resolveSafeHttpUrl("http://8.8.8.8/x", { allowHttp: true });
    expect(out.url.protocol).toBe("http:");
  });

  it.each(["ftp://example.com", "file:///etc/passwd", "javascript:alert(1)"])(
    "rejects scheme %s even when allowHttp is true",
    async (input) => {
      await expect(
        resolveSafeHttpUrl(input, { allowHttp: true }),
      ).rejects.toThrow(UnsafeUrlError);
    },
  );

  it.each([
    ["http://localhost/x", "literal localhost"],
    ["https://myhost.local/x", ".local mDNS"],
    ["https://api.internal/x", ".internal corporate"],
    ["http://something.localhost/x", "*.localhost"],
  ])("rejects internal hostname %s (%s)", async (input) => {
    await expect(
      resolveSafeHttpUrl(input, { allowHttp: true }),
    ).rejects.toThrow(/not reachable from a tenant context/i);
  });
});

describe("resolveSafeHttpUrl — IP literal short-circuit", () => {
  it("accepts a public IP literal without DNS lookup", async () => {
    const out = await resolveSafeHttpUrl("https://8.8.8.8/v1");
    expect(out.addresses).toHaveLength(1);
    expect(out.addresses[0].address).toBe("8.8.8.8");
    expect(out.addresses[0].family).toBe(4);
  });

  it("rejects a private IP literal", async () => {
    await expect(resolveSafeHttpUrl("https://10.0.0.5/admin")).rejects.toThrow(
      /non-public address/i,
    );
  });

  it("rejects an IPv6 loopback literal (brackets stripped before isIP check)", async () => {
    // Regression: parsed.hostname returns "[::1]" with brackets; without
    // bracket stripping the IP-literal branch was skipped, the host
    // went through dns.lookup, and some resolvers happily returned a
    // public A record for the literal string.
    await expect(resolveSafeHttpUrl("https://[::1]/x")).rejects.toThrow(
      /non-public address/i,
    );
  });

  it("rejects an IPv4-mapped IPv6 literal (cannot be used to bypass v4 blocklist)", async () => {
    // Regression: net.BlockList implicitly maps v4→v6 when checking
    // a v4 address against v6 entries, so we had to drop the
    // ::ffff:0:0/96 entry. Mapped-v4 literals are caught by the
    // explicit "::ffff:" hostname prefix check instead.
    await expect(resolveSafeHttpUrl("https://[::ffff:10.0.0.1]/x")).rejects.toThrow(
      /non-public address/i,
    );
  });
});

describe("resolveSafeHttpUrl — DNS resolution", () => {
  const dnsMod = "dns/promises";
  let lookupSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    const dns = await import(dnsMod);
    lookupSpy = vi.spyOn(dns.default, "lookup");
  });

  afterEach(() => {
    lookupSpy.mockRestore();
  });

  it("accepts a hostname that resolves only to public IPs", async () => {
    lookupSpy.mockResolvedValue([
      { address: "203.0.113.10", family: 4 } as never,
    ]);
    // 203.0.113/24 is TEST-NET-3 and IS in the block list, so override
    // to a real public address.
    lookupSpy.mockResolvedValue([
      { address: "8.8.4.4", family: 4 } as never,
    ]);
    const out = await resolveSafeHttpUrl("https://api.example.com/v1");
    expect(out.addresses[0].address).toBe("8.8.4.4");
  });

  it("rejects when ANY resolved address is private", async () => {
    lookupSpy.mockResolvedValue([
      { address: "8.8.8.8", family: 4 } as never,
      { address: "10.0.0.5", family: 4 } as never, // poisoned A record
    ]);
    await expect(
      resolveSafeHttpUrl("https://api.example.com/v1"),
    ).rejects.toThrow(/non-public address/i);
  });

  it("rejects when DNS resolution fails", async () => {
    lookupSpy.mockRejectedValue(new Error("nxdomain") as never);
    await expect(
      resolveSafeHttpUrl("https://nope-does-not-exist.example.com/v1"),
    ).rejects.toThrow(/did not resolve/i);
  });

  it("rejects when DNS returns no addresses", async () => {
    lookupSpy.mockResolvedValue([]);
    await expect(
      resolveSafeHttpUrl("https://empty.example.com/v1"),
    ).rejects.toThrow(/did not resolve/i);
  });
});

describe("assertSafeHttpUrl — convenience wrapper", () => {
  it("returns the URL when resolveSafeHttpUrl succeeds", async () => {
    const url = await assertSafeHttpUrl("https://8.8.8.8/v1");
    expect(url.hostname).toBe("8.8.8.8");
  });

  it("propagates UnsafeUrlError on rejection", async () => {
    await expect(assertSafeHttpUrl("https://127.0.0.1/x")).rejects.toThrow(
      UnsafeUrlError,
    );
  });
});
