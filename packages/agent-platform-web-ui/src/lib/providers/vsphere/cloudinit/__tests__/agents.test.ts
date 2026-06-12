import { describe, expect, it } from "vitest";
import {
  AGENT_PKG_BASE_URL_TOKEN,
  AGENT_USER_TOKEN,
  getAgentRuncmd,
  isKnownAgentType,
  KNOWN_AGENT_TYPES,
} from "../agents";

// The registry is PURE: it returns commands carrying {{AGENT_PKG_BASE_URL}} /
// {{AGENT_USER}} placeholders, resolved server-side in index.ts. So these tests
// need no env and getAgentRuncmd must never throw for a known type (it is also
// called from the browser deploy form).

describe("agent registry", () => {
  it("covers xiaoguai and goose (xiaoguai first = preferred)", () => {
    expect(KNOWN_AGENT_TYPES).toEqual(["xiaoguai", "goose"]);
  });

  it("classifies known/unknown types", () => {
    expect(isKnownAgentType("goose")).toBe(true);
    expect(isKnownAgentType("nope")).toBe(false);
  });

  it("returns the goose installer command group", () => {
    const cmds = getAgentRuncmd("goose");
    expect(cmds.length).toBeGreaterThan(0);
    expect(cmds.some((c) => c.includes("goose"))).toBe(true);
  });

  it("both agents install from the mirror placeholder, not github/PyPI (#286)", () => {
    for (const agentType of KNOWN_AGENT_TYPES) {
      const joined = getAgentRuncmd(agentType).join("\n");
      expect(joined).toContain(AGENT_PKG_BASE_URL_TOKEN);
      expect(joined).not.toContain("github.com");
      expect(joined).not.toContain("pypi.org");
      expect(joined).not.toMatch(/pip install/);
    }
  });

  it("goose install is sha256-verified and a root system install (#228)", () => {
    const joined = getAgentRuncmd("goose").join("\n");
    // bytes pinned via sha256 (also detects a tampered mirror); version no
    // longer needs to appear in the URL path.
    expect(joined).toMatch(/sha256sum -c/);
    expect(joined).toMatch(/[0-9a-f]{64}/);
    expect(joined).toContain("/usr/local/bin/goose");
    // system install, no su to a user.
    expect(joined).not.toContain(AGENT_USER_TOKEN);
  });

  it("xiaoguai installs the pinned tarball into the user's home via su", () => {
    const joined = getAgentRuncmd("xiaoguai").join("\n");
    expect(joined).toContain(`su - ${AGENT_USER_TOKEN} -c`);
    expect(joined).toMatch(/xiaoguai-v\d+\.\d+\.\d+-x86_64-unknown-linux-gnu\.tar\.gz/);
    expect(joined).toContain("~/.local/bin");
    expect(joined).toMatch(/tar -xzf/);
  });

  it("is pure — never throws for a known type even with no env (browser-safe)", () => {
    const saved = process.env.AGENT_PKG_BASE_URL;
    delete process.env.AGENT_PKG_BASE_URL;
    try {
      expect(() => getAgentRuncmd("xiaoguai")).not.toThrow();
      expect(() => getAgentRuncmd("goose")).not.toThrow();
    } finally {
      if (saved !== undefined) process.env.AGENT_PKG_BASE_URL = saved;
    }
  });

  it("no agent's install commands pipe a download into a shell (#228 acceptance)", () => {
    for (const agentType of KNOWN_AGENT_TYPES) {
      for (const cmd of getAgentRuncmd(agentType)) {
        expect(cmd).not.toMatch(/curl[^\n|]*\|\s*(\S+=\S+\s+)*(ba)?sh\b/);
        expect(cmd).not.toMatch(/wget[^\n|]*\|\s*(\S+=\S+\s+)*(ba)?sh\b/);
      }
    }
  });

  it("throws a teaching error for an unknown type", () => {
    expect(() => getAgentRuncmd("k8s")).toThrow(/Known types: xiaoguai, goose/);
  });
});
