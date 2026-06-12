import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildDeployCloudInit } from "../index";

// Stable marker for "the goose install commands are present" — the install
// target is fixed even though the download URL is now the internal mirror.
const GOOSE_MARK = "/usr/local/bin/goose";
const MIRROR = "http://mirror.test/agents";

const base = {
  instanceId: "inst-1",
  hostname: "web-01",
  network: { mode: "dhcp" } as const,
  user: "ops",
  password: "pw",
  passwordSalt: "fixedsalt",
};

// 64-hex sha256 fixture for the xiaoguai artifact gate (SEC-3).
const XG_SHA = "a".repeat(64);

beforeEach(() => {
  process.env.AGENT_PKG_BASE_URL = MIRROR;
  process.env.AGENT_XIAOGUAI_SHA256 = XG_SHA;
});
afterEach(() => {
  delete process.env.AGENT_PKG_BASE_URL;
  delete process.env.AGENT_XIAOGUAI_SHA256;
});

describe("buildDeployCloudInit", () => {
  it("returns a userdata + metadata pair", () => {
    const { userdata, metadata } = buildDeployCloudInit(base);
    expect(userdata.startsWith("#cloud-config")).toBe(true);
    expect(metadata).toContain("instance-id: 'inst-1'");
    expect(metadata).toContain("local-hostname: 'web-01'");
  });

  it("injects a known agent's install commands into the runcmd tail", () => {
    const { userdata } = buildDeployCloudInit({ ...base, agentType: "goose" });
    expect(userdata).toContain(GOOSE_MARK);
    // base runcmd step still comes first
    const runcmdIdx = userdata.indexOf("runcmd:");
    const systemdIdx = userdata.indexOf("systemctl enable --now open-vm-tools");
    const gooseIdx = userdata.indexOf(GOOSE_MARK);
    expect(runcmdIdx).toBeLessThan(systemdIdx);
    expect(systemdIdx).toBeLessThan(gooseIdx);
  });

  it("appends free-form install commands after the registry group", () => {
    const { userdata } = buildDeployCloudInit({
      ...base,
      agentType: "goose",
      installCommands: ['echo "post-install"'],
    });
    expect(userdata.indexOf(GOOSE_MARK)).toBeLessThan(userdata.indexOf("post-install"));
  });

  it("uses only free-form commands when no agentType is given", () => {
    const { userdata } = buildDeployCloudInit({
      ...base,
      installCommands: ["systemctl restart myagent"],
    });
    expect(userdata).toContain("systemctl restart myagent");
    expect(userdata).not.toContain(GOOSE_MARK);
  });
});

describe("{{AGENT_USER}} substitution (#286, doc 33 §4.1)", () => {
  it("xiaoguai su's to the deploy osUser, leaving no placeholder behind", () => {
    const { userdata } = buildDeployCloudInit({ ...base, user: "alice", agentType: "xiaoguai" });
    expect(userdata).toContain("su - alice -c");
    expect(userdata).not.toContain("{{AGENT_USER}}");
  });

  it("substitutes the placeholder in free-form install commands too", () => {
    const { userdata } = buildDeployCloudInit({
      ...base,
      user: "deployer",
      installCommands: ["su - {{AGENT_USER}} -c 'whoami'"],
    });
    expect(userdata).toContain("su - deployer -c");
    expect(userdata).not.toContain("{{AGENT_USER}}");
  });

  it("rejects an invalid Linux username before it reaches the VM command", () => {
    expect(() =>
      buildDeployCloudInit({ ...base, user: "a; rm -rf /", agentType: "xiaoguai" }),
    ).toThrow(/not a valid Linux username/);
  });

  it("leaves commands untouched when no placeholder is present (goose)", () => {
    // goose is a system install — an odd username must NOT block it, since it
    // never gets spliced into a command.
    const { userdata } = buildDeployCloudInit({ ...base, user: "Weird User!", agentType: "goose" });
    expect(userdata).toContain(GOOSE_MARK);
  });
});

describe("{{AGENT_PKG_BASE_URL}} substitution (#286, server-side mirror resolution)", () => {
  it("resolves the mirror placeholder to AGENT_PKG_BASE_URL, leaving none behind", () => {
    const { userdata } = buildDeployCloudInit({ ...base, agentType: "goose" });
    expect(userdata).toContain(`${MIRROR}/goose-x86_64-unknown-linux-gnu.tar.bz2`);
    expect(userdata).not.toContain("{{AGENT_PKG_BASE_URL}}");
  });

  it("substitutes the placeholder in free-form install commands too", () => {
    const { userdata } = buildDeployCloudInit({
      ...base,
      installCommands: ["curl {{AGENT_PKG_BASE_URL}}/extra.sh -o /tmp/e"],
    });
    expect(userdata).toContain(`${MIRROR}/extra.sh`);
    expect(userdata).not.toContain("{{AGENT_PKG_BASE_URL}}");
  });

  it("fails fast when a known agent needs the mirror but it is unset", () => {
    delete process.env.AGENT_PKG_BASE_URL;
    expect(() => buildDeployCloudInit({ ...base, agentType: "xiaoguai" })).toThrow(
      /AGENT_PKG_BASE_URL is not set/,
    );
  });

  it("rejects a mirror URL carrying shell metacharacters", () => {
    process.env.AGENT_PKG_BASE_URL = "http://mirror.test/$(reboot)";
    expect(() => buildDeployCloudInit({ ...base, agentType: "goose" })).toThrow(/not a plain http/);
  });

  it("tolerates a trailing slash on the mirror URL", () => {
    process.env.AGENT_PKG_BASE_URL = `${MIRROR}/`;
    const { userdata } = buildDeployCloudInit({ ...base, agentType: "goose" });
    expect(userdata).toContain(`${MIRROR}/goose-x86_64-unknown-linux-gnu.tar.bz2`);
    expect(userdata).not.toContain(`${MIRROR}//goose`);
  });

  it("does not require the mirror when no known agent / placeholder is used", () => {
    delete process.env.AGENT_PKG_BASE_URL;
    const { userdata } = buildDeployCloudInit({
      ...base,
      installCommands: ["systemctl restart myagent"],
    });
    expect(userdata).toContain("systemctl restart myagent");
  });
});

describe("{{XIAOGUAI_SHA256}} substitution (SEC-3, supply-chain gate)", () => {
  it("gates the xiaoguai download with sha256sum -c before extract, no placeholder left", () => {
    const { userdata } = buildDeployCloudInit({ ...base, user: "alice", agentType: "xiaoguai" });
    expect(userdata).toContain(`${XG_SHA}  /tmp/xiaoguai.tgz' | sha256sum -c -`);
    // the gate must precede tar extraction (fail-closed ordering)
    expect(userdata.indexOf("sha256sum -c -")).toBeLessThan(userdata.indexOf("tar -xzf"));
    expect(userdata).not.toContain("{{XIAOGUAI_SHA256}}");
  });

  it("fails fast when xiaoguai is requested but the hash is unset", () => {
    delete process.env.AGENT_XIAOGUAI_SHA256;
    expect(() => buildDeployCloudInit({ ...base, agentType: "xiaoguai" })).toThrow(
      /AGENT_XIAOGUAI_SHA256 is not set/,
    );
  });

  it("rejects a malformed (non-64-hex) hash", () => {
    process.env.AGENT_XIAOGUAI_SHA256 = "not-a-real-hash";
    expect(() => buildDeployCloudInit({ ...base, agentType: "xiaoguai" })).toThrow(
      /not a 64-char lowercase hex/,
    );
  });

  it("does not require the hash for goose (no placeholder present)", () => {
    delete process.env.AGENT_XIAOGUAI_SHA256;
    const { userdata } = buildDeployCloudInit({ ...base, agentType: "goose" });
    expect(userdata).toContain(GOOSE_MARK);
  });
});

describe("bootstrap register callback (#231)", () => {
  const bootstrap = {
    token: "aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233",
    controlPlaneUrl: "https://cp.example.invalid",
  };

  it("appends the register curl AFTER agent install commands", () => {
    const { userdata } = buildDeployCloudInit({ ...base, agentType: "goose", bootstrap });
    expect(userdata).toContain("/api/nodes/register");
    expect(userdata).toContain(`Bearer ${bootstrap.token}`);
    // self-report READY only after the agent install finished
    expect(userdata.indexOf(GOOSE_MARK)).toBeLessThan(userdata.indexOf("/api/nodes/register"));
  });

  it("register failure is non-fatal for cloud-init (worker path still marks RUNNING)", () => {
    const { userdata } = buildDeployCloudInit({ ...base, bootstrap });
    const line = userdata.split("\n").find((l) => l.includes("/api/nodes/register")) ?? "";
    expect(line).toMatch(/\|\|/); // has an || fallback so the runcmd entry exits 0
  });

  it("payload JSON quotes are shell-escaped (regression: bare quotes get eaten by sh)", () => {
    const { userdata } = buildDeployCloudInit({ ...base, bootstrap });
    // Runtime command must carry backslash-quote around JSON keys so the
    // shell's outer -d "..." quoting preserves them. In the emitted YAML
    // text (one more escaping layer) that appears as 3 backslashes + quote.
    const BS = "\\"; // one literal backslash
    expect(userdata).toContain(BS + BS + BS + '"instanceId'); // \\\"instanceId
    expect(userdata).not.toContain(BS + '"{' + BS + '"instanceId'); // broken bare-quote form: \"{\"instanceId
  });

  it("shell metacharacters in controlPlaneUrl are rejected (PR #253 CRITICAL-1)", () => {
    for (const evil of [
      'https://cp.example.invalid"$(id)',
      "https://cp.example.invalid'; rm -rf /; '",
      "https://cp.example.invalid`id`",
      "https://cp.example.invalid $(curl evil)",
    ]) {
      expect(() =>
        buildDeployCloudInit({ ...base, bootstrap: { token: "ab".repeat(32), controlPlaneUrl: evil } })
      ).toThrow(/not a plain http/);
    }
  });

  it("no bootstrap → no register callback in userdata", () => {
    const { userdata } = buildDeployCloudInit({ ...base, agentType: "goose" });
    expect(userdata).not.toContain("/api/nodes/register");
  });
});
