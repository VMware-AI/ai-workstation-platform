import { describe, expect, it } from "vitest";
import { CSV_COLUMNS, netmaskToPrefix, parseDeployCsv } from "../csv";

const HEADER = CSV_COLUMNS.join(",");

describe("netmaskToPrefix", () => {
  it.each([
    ["255.255.255.0", 24],
    ["255.255.0.0", 16],
    ["255.255.255.252", 30],
    ["255.255.255.255", 32],
    ["0.0.0.0", 0],
    ["24", 24], // bare prefix
    ["/24", 24], // slash prefix
  ])("converts %s -> %d", (mask, expected) => {
    expect(netmaskToPrefix(mask)).toBe(expected);
  });

  it.each([
    ["255.0.255.0", "non-contiguous"],
    ["255.255.255", "too few octets"],
    ["255.255.255.256", "octet out of range"],
    ["33", "prefix out of range"],
    ["abc", "garbage"],
  ])("rejects %s (%s)", (mask) => {
    expect(netmaskToPrefix(mask)).toBeNull();
  });
});

describe("parseDeployCsv", () => {
  it("parses a static row", () => {
    const csv = [
      HEADER,
      "web-01,10.0.1.5,255.255.255.0,10.0.1.1,8.8.8.8,ops,pw,ssh-ed25519 AAA user@h",
    ].join("\n");
    const { rows, errors } = parseDeployCsv(csv);
    expect(errors).toEqual([]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      line: 2,
      vmName: "web-01",
      hostname: "web-01",
      user: "ops",
      password: "pw",
      sshKey: "ssh-ed25519 AAA user@h",
      network: { mode: "static", ip: "10.0.1.5", prefix: 24, gateway: "10.0.1.1", dns: ["8.8.8.8"] },
    });
  });

  it("treats an empty ip as DHCP and omits absent ssh key", () => {
    const csv = [HEADER, "web-02,,,,,ops,pw,"].join("\n");
    const { rows, errors } = parseDeployCsv(csv);
    expect(errors).toEqual([]);
    expect(rows[0].network).toEqual({ mode: "dhcp" });
    expect(rows[0].sshKey).toBeUndefined();
  });

  it("splits multiple DNS servers on whitespace or semicolons", () => {
    const csv = [
      HEADER,
      "web-03,10.0.1.6,255.255.255.0,10.0.1.1,8.8.8.8;1.1.1.1,ops,pw,",
      "web-04,10.0.1.7,255.255.255.0,10.0.1.1,8.8.8.8 1.1.1.1,ops,pw,",
    ].join("\n");
    const { rows } = parseDeployCsv(csv);
    expect((rows[0].network as { dns: string[] }).dns).toEqual(["8.8.8.8", "1.1.1.1"]);
    expect((rows[1].network as { dns: string[] }).dns).toEqual(["8.8.8.8", "1.1.1.1"]);
  });

  it("preserves commas inside a quoted field", () => {
    const csv = [HEADER, 'web-05,,,,,ops,"p,w,d",'].join("\n");
    const { rows } = parseDeployCsv(csv);
    expect(rows[0].password).toBe("p,w,d");
  });

  it("collects per-row errors with line numbers and keeps good rows", () => {
    const csv = [
      HEADER,
      "good-1,,,,,ops,pw,",
      ",,,,,ops,pw,", // missing vm_name
      "bad-net,10.0.1.5,255.0.255.0,10.0.1.1,8.8.8.8,ops,pw,", // bad mask
      "no-pass,,,,,ops,,", // missing password
      "good-2,,,,,ops,pw,",
    ].join("\n");
    const { rows, errors } = parseDeployCsv(csv);
    expect(rows.map((r) => r.vmName)).toEqual(["good-1", "good-2"]);
    expect(errors.map((e) => e.line)).toEqual([3, 4, 5]);
    expect(errors[0].message).toMatch(/vm_name/);
    expect(errors[1].message).toMatch(/netmask/);
    expect(errors[2].message).toMatch(/password/);
  });

  it("rejects a bad header", () => {
    const { rows, errors } = parseDeployCsv("name,ip\nweb,1.2.3.4");
    expect(rows).toEqual([]);
    expect(errors[0].message).toMatch(/Header must be/);
  });

  it("rejects unsafe characters in identity fields", () => {
    const csv = [HEADER, "ev'il,,,,,ops,pw,"].join("\n");
    const { errors } = parseDeployCsv(csv);
    expect(errors[0].message).toMatch(/unsafe/);
  });

  it("skips blank lines and ignores a trailing newline", () => {
    const csv = [HEADER, "web-06,,,,,ops,pw,", "", ""].join("\n");
    const { rows, errors } = parseDeployCsv(csv);
    expect(errors).toEqual([]);
    expect(rows).toHaveLength(1);
  });
});
