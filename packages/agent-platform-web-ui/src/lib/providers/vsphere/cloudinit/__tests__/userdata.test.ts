import { describe, expect, it } from "vitest";
import {
  generateMetadata,
  generateNetworkConfig,
  generateUserdata,
} from "../userdata";
import { YamlFieldError } from "../yaml-safe";

describe("generateNetworkConfig", () => {
  it("renders a DHCP block", () => {
    expect(generateNetworkConfig({ mode: "dhcp" })).toBe(
      [
        "version: 2",
        "ethernets:",
        "  mainif:",
        "    match:",
        '      name: "en*"',
        "    dhcp4: true",
        "    dhcp6: false",
      ].join("\n"),
    );
  });

  it("renders a static block with a single DNS server", () => {
    const out = generateNetworkConfig({
      mode: "static",
      ip: "10.0.1.5",
      prefix: 24,
      gateway: "10.0.1.1",
      dns: ["10.0.1.2"],
    });
    expect(out).toContain("    dhcp4: false");
    expect(out).toContain("      - '10.0.1.5/24'");
    expect(out).toContain("        via: '10.0.1.1'");
    expect(out).toContain("        - '10.0.1.2'");
  });

  it("includes a second DNS server when provided", () => {
    const out = generateNetworkConfig({
      mode: "static",
      ip: "10.0.1.5",
      prefix: 24,
      gateway: "10.0.1.1",
      dns: ["10.0.1.2", "8.8.8.8"],
    });
    expect(out).toContain("        - '10.0.1.2'");
    expect(out).toContain("        - '8.8.8.8'");
  });

  it("rejects an out-of-range prefix and empty DNS", () => {
    expect(() =>
      generateNetworkConfig({ mode: "static", ip: "10.0.1.5", prefix: 33, gateway: "g", dns: ["d"] }),
    ).toThrow(/prefix/);
    expect(() =>
      generateNetworkConfig({ mode: "static", ip: "10.0.1.5", prefix: 24, gateway: "g", dns: [] }),
    ).toThrow(/DNS/);
  });
});

describe("generateMetadata", () => {
  it("emits instance-id, hostname and an indented network block", () => {
    const out = generateMetadata({
      instanceId: "inst-123",
      hostname: "web-01",
      network: { mode: "dhcp" },
    });
    expect(out).toContain("instance-id: 'inst-123'");
    expect(out).toContain("local-hostname: 'web-01'");
    expect(out).toContain("network:");
    // network config nested two spaces under `network:`
    expect(out).toContain("  version: 2");
    expect(out).toContain("    dhcp4: true");
  });
});

describe("generateUserdata", () => {
  const base = {
    hostname: "web-01",
    user: "ops",
    password: "s3cret",
    passwordSalt: "fixedsalt",
  };

  it("emits a #cloud-config with hashed password and defaults", () => {
    const out = generateUserdata(base);
    expect(out.startsWith("#cloud-config\n")).toBe(true);
    expect(out).toContain("hostname: 'web-01'");
    expect(out).toContain("timezone: 'Asia/Shanghai'");
    expect(out).toContain("  - name: 'ops'");
    expect(out).toMatch(/passwd: '\$6\$fixedsalt\$[./0-9A-Za-z]{86}'/);
    expect(out).toContain("  - open-vm-tools");
    expect(out).toContain("  - systemctl enable --now open-vm-tools");
    // no plaintext password leaks into the document
    expect(out).not.toContain("s3cret");
  });

  it("omits ssh_authorized_keys when no key is given", () => {
    expect(generateUserdata(base)).not.toContain("ssh_authorized_keys");
  });

  it("includes the ssh key when provided", () => {
    const out = generateUserdata({ ...base, sshKey: "ssh-ed25519 AAAAC3 user@host" });
    expect(out).toContain("    ssh_authorized_keys:");
    expect(out).toContain("      - 'ssh-ed25519 AAAAC3 user@host'");
  });

  it("appends extra packages after the defaults (validated, quoted)", () => {
    const out = generateUserdata({ ...base, packages: ["htop", "tmux"] });
    expect(out).toContain("  - 'htop'");
    expect(out).toContain("  - 'tmux'");
  });

  it("appends and safely encodes extra runcmd entries", () => {
    const cmd = 'curl -fsSL https://x/y.sh | bash && echo "ok"';
    const out = generateUserdata({ ...base, runcmd: [cmd] });
    const line = out.split("\n").find((l) => l.startsWith("  - ") && l.includes("-fsSL"));
    expect(line).toBeDefined();
    // entry is a JSON/YAML double-quoted scalar round-tripping to the original
    expect(JSON.parse(line!.replace(/^ {2}- /, ""))).toBe(cmd);
  });

  it("formats and mounts each data disk (sdb→/data, sdc→/data1)", () => {
    const out = generateUserdata({ ...base, dataDisks: 2 });
    expect(out).toContain("/dev/sdb");
    expect(out).toContain("/dev/sdc");
    expect(out).toContain("mkfs.ext4 -F /dev/sdb");
    // first disk → /data, second → /data1
    expect(out).toContain("mount /data");
    expect(out).toContain("mount /data1");
  });

  it("rejects an out-of-range data disk count", () => {
    expect(() => generateUserdata({ ...base, dataDisks: 10 })).toThrow(/dataDisks/);
  });

  it("rejects fixed fields containing injection characters", () => {
    expect(() => generateUserdata({ ...base, hostname: "ev'il" })).toThrow(YamlFieldError);
  });
});
