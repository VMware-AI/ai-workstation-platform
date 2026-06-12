import { describe, expect, it } from "vitest";
import { encodeRuncmd, isYamlSafe, YamlFieldError, yamlField } from "../yaml-safe";

describe("yamlField", () => {
  it("single-quotes a safe value", () => {
    expect(yamlField("hostname", "web-01")).toBe("'web-01'");
  });

  it.each([
    ["single quote", "a'b"],
    ["double quote", 'a"b'],
    ["backslash", "a\\b"],
    ["dollar", "a$b"],
    ["backtick", "a`b"],
    ["newline", "a\nb"],
    ["carriage return", "a\rb"],
  ])("rejects %s", (_label, value) => {
    expect(() => yamlField("field", value)).toThrow(YamlFieldError);
  });

  it("error message names the field and shows the bad value", () => {
    expect(() => yamlField("hostname", "ev'il")).toThrow(/hostname/);
  });
});

describe("isYamlSafe", () => {
  it("classifies values", () => {
    expect(isYamlSafe("plain.value-1")).toBe(true);
    expect(isYamlSafe("ssh-rsa AAAA+/abc= user@host")).toBe(true);
    expect(isYamlSafe("has'quote")).toBe(false);
  });
});

describe("encodeRuncmd", () => {
  it("encodes arbitrary shell as a parseable double-quoted scalar", () => {
    const cmd = 'curl -fsSL https://x/y.sh | bash && echo "done"';
    const encoded = encodeRuncmd(cmd);
    expect(encoded.startsWith('"')).toBe(true);
    expect(JSON.parse(encoded)).toBe(cmd);
  });

  it("neutralises injection attempts (no raw breakout)", () => {
    const cmd = 'x"]\nrm -rf /\n  - ["evil';
    const encoded = encodeRuncmd(cmd);
    expect(encoded).not.toContain("\n");
    expect(JSON.parse(encoded)).toBe(cmd);
  });
});
