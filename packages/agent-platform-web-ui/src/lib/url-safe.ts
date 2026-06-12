import net from "net";
import dns from "dns/promises";
import type { LookupAddress } from "dns";

// CIDR ranges that the agent-platform-web-ui server MUST NOT fetch on behalf of a tenant
// when validating a user-supplied LLM provider endpoint. Covers loopback,
// RFC1918, link-local, CGNAT, multicast, unspecified, and IPv6 equivalents.
const blockedV4: ReadonlyArray<[string, number]> = [
  ["0.0.0.0", 8],        // unspecified / "this network"
  ["10.0.0.0", 8],        // RFC1918 private
  ["100.64.0.0", 10],     // CGNAT
  ["127.0.0.0", 8],       // loopback
  ["169.254.0.0", 16],    // link-local
  ["172.16.0.0", 12],     // RFC1918 private
  ["192.0.0.0", 24],      // IETF protocol assignments
  ["192.0.2.0", 24],      // TEST-NET-1
  ["192.168.0.0", 16],    // RFC1918 private
  ["198.18.0.0", 15],     // benchmarking
  ["198.51.100.0", 24],   // TEST-NET-2
  ["203.0.113.0", 24],    // TEST-NET-3
  ["224.0.0.0", 4],       // multicast
  ["240.0.0.0", 4],       // reserved + broadcast
];

const blockedV6: ReadonlyArray<[string, number]> = [
  ["::", 128],            // unspecified
  ["::1", 128],           // loopback
  // Intentionally NOT adding ::ffff:0:0/96 here: Node's
  // net.BlockList.check(v4, "ipv4") implicitly maps v4 → v6 mapped form
  // before matching v6 entries, so a /96 mapped-v4 entry blocks EVERY
  // IPv4 address regardless of value (8.8.8.8 → ::ffff:8.8.8.8 → in /96).
  // Mapped-v4 URL hosts are caught explicitly by the "::ffff:" hostname
  // check in resolveSafeHttpUrl instead.
  ["64:ff9b::", 96],      // NAT64
  ["fc00::", 7],          // unique local
  ["fe80::", 10],          // link-local
  ["ff00::", 8],           // multicast
];

const blockList = new net.BlockList();
for (const [addr, prefix] of blockedV4) blockList.addSubnet(addr, prefix, "ipv4");
for (const [addr, prefix] of blockedV6) blockList.addSubnet(addr, prefix, "ipv6");

export function isBlockedIp(ip: string): boolean {
  const family = net.isIP(ip);
  if (family === 0) return true; // not a valid IP literal
  return blockList.check(ip, family === 4 ? "ipv4" : "ipv6");
}

export class UnsafeUrlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnsafeUrlError";
  }
}

export interface SafeUrlOptions {
  // Allow plain http: in addition to https:. Default false — production
  // provider endpoints should be https only.
  allowHttp?: boolean;
}

export interface SafeResolvedUrl {
  url: URL;
  addresses: LookupAddress[];
}

// Validates a user-supplied URL is safe for the server to fetch:
//   1. Parseable + scheme is https (or http when allowHttp is true).
//   2. Host is not an obvious internal name (localhost, *.local, *.internal).
//   3. DNS resolves only to public IPs (no private/loopback/link-local/etc).
// Throws UnsafeUrlError on rejection with a message safe to surface to the
// client. The caller should call this both at write-time (when the endpoint
// is first persisted) and at fetch-time (to narrow DNS-rebinding windows).
export async function resolveSafeHttpUrl(
  input: string,
  opts: SafeUrlOptions = {}
): Promise<SafeResolvedUrl> {
  let parsed: URL;
  try {
    parsed = new URL(input);
  } catch {
    throw new UnsafeUrlError("Endpoint is not a valid URL.");
  }

  const allowedProtocols = opts.allowHttp ? ["http:", "https:"] : ["https:"];
  if (!allowedProtocols.includes(parsed.protocol)) {
    throw new UnsafeUrlError(
      opts.allowHttp
        ? "Endpoint must use http:// or https://."
        : "Endpoint must use https://."
    );
  }

  // parsed.hostname returns the IPv6 literal WITH brackets ("[::1]"),
  // which makes net.isIP() return 0 and dns.lookup() reach for it as a
  // string hostname (some recursive resolvers happily map it to a real
  // public A record). Strip brackets so the IP-literal branch fires.
  let host = parsed.hostname.toLowerCase();
  if (host.startsWith("[") && host.endsWith("]")) {
    host = host.slice(1, -1);
  }
  if (
    host === "localhost" ||
    host === "ip6-localhost" ||
    host.endsWith(".local") ||
    host.endsWith(".localhost") ||
    host.endsWith(".internal")
  ) {
    throw new UnsafeUrlError("Endpoint hostname is not reachable from a tenant context.");
  }

  // If the host is an IP literal, check it directly without DNS.
  if (net.isIP(host) !== 0) {
    // Reject IPv4-mapped IPv6 literals outright (::ffff:x.x.x.x).
    // The mapping is only an obfuscation surface — every legitimate
    // server has a non-mapped form, and the mapped form would let an
    // attacker reach ::ffff:10.0.0.1 to bypass the v4 blocklist
    // (since the address is technically a v6 literal).
    if (host.startsWith("::ffff:") || host.startsWith("::FFFF:")) {
      throw new UnsafeUrlError("Endpoint resolves to a non-public address.");
    }
    if (isBlockedIp(host)) {
      throw new UnsafeUrlError("Endpoint resolves to a non-public address.");
    }
    return { url: parsed, addresses: [{ address: host, family: net.isIP(host) }] };
  }

  let addrs: LookupAddress[];
  try {
    addrs = await dns.lookup(host, { all: true, verbatim: true });
  } catch {
    throw new UnsafeUrlError("Endpoint hostname did not resolve.");
  }
  if (addrs.length === 0) {
    throw new UnsafeUrlError("Endpoint hostname did not resolve.");
  }
  for (const a of addrs) {
    if (isBlockedIp(a.address)) {
      throw new UnsafeUrlError("Endpoint resolves to a non-public address.");
    }
  }
  return { url: parsed, addresses: addrs };
}

export async function assertSafeHttpUrl(
  input: string,
  opts: SafeUrlOptions = {}
): Promise<URL> {
  const resolved = await resolveSafeHttpUrl(input, opts);
  return resolved.url;
}
