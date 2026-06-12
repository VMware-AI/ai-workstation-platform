import { describe, it, expect } from "vitest";
import { teachVcError } from "@/lib/providers/vsphere/teach-vc-error";

describe("teachVcError (#357 item 7)", () => {
  it("maps an auth failure to a credential hint", () => {
    expect(teachVcError("ServerFaultCode: Cannot complete login due to an incorrect user name")).toMatch(
      /认证失败/
    );
    expect(teachVcError("vSphere API /session returned 401: secret-internal-body")).toMatch(/认证失败/);
  });

  it("maps a TLS/cert error, honoring a custom hint", () => {
    expect(teachVcError("x509: certificate signed by unknown authority")).toMatch(/证书/);
    expect(teachVcError("self-signed certificate", { certHint: "勾选跳过证书校验" })).toBe("勾选跳过证书校验");
  });

  it("maps DNS and connectivity failures", () => {
    expect(teachVcError("getaddrinfo ENOTFOUND vc.invalid")).toMatch(/无法解析主机名/);
    expect(teachVcError("dial tcp 10.0.0.1:443: i/o timeout")).toMatch(/无法连接/);
  });

  it("NEVER echoes raw upstream text in the fallback (no stderr leak)", () => {
    const rawStderr = "govc: /datacenter/secret-host internal stack trace details";
    const msg = teachVcError(rawStderr);
    expect(msg).not.toContain("secret-host");
    expect(msg).not.toContain("internal stack trace");
    expect(msg).toMatch(/连接 vCenter 失败/);
  });
});
