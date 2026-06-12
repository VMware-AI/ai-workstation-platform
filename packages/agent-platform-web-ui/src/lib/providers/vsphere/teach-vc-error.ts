// Shared vCenter/govc error-to-teaching-message mapping (#357 item 7).
//
// Both the "测试 VC" path (VSphereClient over the REST API) and the "浏览资源"
// path (govc CLI) used to surface raw upstream text to the browser in their
// fallback branch — `连接失败：${raw}` / `govc 失败：${stderr}`. The raw stderr
// can carry internal infra details (hostnames, paths, stack noise). This maps
// the common failure classes to an actionable Chinese message and, crucially,
// returns a GENERIC fallback instead of echoing the raw text.

export interface TeachVcOptions {
  // false → caller treats verifySsl as configurable in the pool form, so the
  // cert message points there; true → the form has its own checkbox.
  certHint?: string;
}

const DEFAULT_CERT_HINT =
  "TLS 证书校验失败：自签名证书环境请勾选/关闭证书校验（verifySsl=false）。";

const GENERIC_FALLBACK = "连接 vCenter 失败：请检查地址、凭据、网络与证书设置，详情见服务端日志。";

export function teachVcError(raw: string, opts: TeachVcOptions = {}): string {
  const m = raw.toLowerCase();
  if (
    m.includes("401") ||
    m.includes("unauthor") ||
    m.includes("incorrect") ||
    m.includes("credentials") ||
    m.includes("permission") ||
    m.includes("login") ||
    m.includes("authentication")
  ) {
    return "认证失败：用户名或密码错误，或权限不足。";
  }
  if (
    m.includes("self-signed") ||
    m.includes("self signed") ||
    m.includes("certificate") ||
    m.includes("x509") ||
    m.includes("unable to verify") ||
    m.includes("cert")
  ) {
    return opts.certHint ?? DEFAULT_CERT_HINT;
  }
  if (
    m.includes("enotfound") ||
    m.includes("getaddrinfo") ||
    m.includes("eai_again") ||
    m.includes("no such host") ||
    m.includes("lookup")
  ) {
    return "无法解析主机名：检查 vCenter 地址是否正确。";
  }
  if (
    m.includes("econnrefused") ||
    m.includes("connection refused") ||
    m.includes("timed out") ||
    m.includes("etimedout") ||
    m.includes("timeout") ||
    m.includes("i/o timeout") ||
    m.includes("dial tcp") ||
    m.includes("network") ||
    m.includes("fetch failed")
  ) {
    return "无法连接到 vCenter：检查地址、网络可达性与 443 端口。";
  }
  // Never echo the raw upstream text to the browser.
  return GENERIC_FALLBACK;
}
