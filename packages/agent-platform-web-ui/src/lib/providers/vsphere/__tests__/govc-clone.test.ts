import { describe, expect, it } from "vitest";
import { gunzipSync } from "node:zlib";
import {
  encodeGuestinfo,
  cloneOff,
  injectGuestinfo,
  waitForIp,
  type GovcRunner,
} from "../govc";

function recorder(impl?: (args: string[]) => string): { run: GovcRunner; calls: string[][] } {
  const calls: string[][] = [];
  const run: GovcRunner = async (args) => {
    calls.push(args);
    return impl ? impl(args) : "";
  };
  return { run, calls };
}

describe("encodeGuestinfo", () => {
  it("gzip+base64 round-trips back to the original", () => {
    const text = "#cloud-config\nhostname: 'web-01'\n";
    const encoded = encodeGuestinfo(text);
    expect(encoded).toMatch(/^[A-Za-z0-9+/]+=*$/); // base64
    expect(gunzipSync(Buffer.from(encoded, "base64")).toString("utf8")).toBe(text);
  });
});

describe("cloneOff", () => {
  it("builds a powered-off clone with only the supplied placement flags", async () => {
    const { run, calls } = recorder();
    await cloneOff(run, {
      templateName: "ubuntu-tmpl",
      name: "web-01",
      datastore: "ds1",
      network: "VM Network",
    });
    expect(calls[0]).toEqual([
      "vm.clone",
      "-vm",
      "ubuntu-tmpl",
      "-on=false",
      "-ds",
      "ds1",
      "-net",
      "VM Network",
      "web-01",
    ]);
  });

  it("omits unset placement flags", async () => {
    const { run, calls } = recorder();
    await cloneOff(run, { templateName: "t", name: "vm" });
    expect(calls[0]).toEqual(["vm.clone", "-vm", "t", "-on=false", "vm"]);
  });
});

describe("injectGuestinfo", () => {
  it("sets both docs with gzip+base64 encoding markers", async () => {
    const { run, calls } = recorder();
    await injectGuestinfo(run, "web-01", { userdata: "USER", metadata: "META" });
    const args = calls[0];
    expect(args.slice(0, 3)).toEqual(["vm.change", "-vm", "web-01"]);
    expect(args).toContain("guestinfo.userdata.encoding=gzip+base64");
    expect(args).toContain("guestinfo.metadata.encoding=gzip+base64");
    const ud = args.find((a) => a.startsWith("guestinfo.userdata="))!.slice("guestinfo.userdata=".length);
    expect(gunzipSync(Buffer.from(ud, "base64")).toString()).toBe("USER");
  });
});

describe("waitForIp", () => {
  it("returns the trimmed IP", async () => {
    const { run } = recorder(() => "10.0.1.7\n");
    expect(await waitForIp(run, "web-01", 60)).toBe("10.0.1.7");
  });

  it("throws when no IP is reported", async () => {
    const { run } = recorder(() => "  \n");
    await expect(waitForIp(run, "web-01", 60)).rejects.toThrow(/IP/);
  });

  it("passes the wait timeout to govc", async () => {
    const { run, calls } = recorder(() => "1.2.3.4");
    await waitForIp(run, "web-01", 120);
    expect(calls[0]).toEqual(["vm.ip", "-wait", "120s", "web-01"]);
  });
});
