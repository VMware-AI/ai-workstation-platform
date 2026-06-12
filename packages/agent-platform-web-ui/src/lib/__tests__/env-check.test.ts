import { describe, it, expect } from "vitest";
import { requiredEnvProblems, assertRequiredEnv } from "../env-check";

const GOOD_KEY = "a".repeat(64); // 64 hex chars = 32 bytes
const GOOD_ENV = { ENCRYPTION_KEY: GOOD_KEY };

describe("requiredEnvProblems", () => {
  it("returns no problems when ENCRYPTION_KEY is valid", () => {
    expect(requiredEnvProblems(GOOD_ENV)).toEqual([]);
  });

  it("flags missing ENCRYPTION_KEY", () => {
    const problems = requiredEnvProblems({});
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("ENCRYPTION_KEY");
    expect(problems[0]).toContain("missing");
  });

  it("flags ENCRYPTION_KEY that does not decode to 32 bytes", () => {
    const problems = requiredEnvProblems({ ENCRYPTION_KEY: "abcd1234" }); // too short
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("ENCRYPTION_KEY");
    expect(problems[0]).toContain("32 bytes");
  });

  it("flags ENCRYPTION_KEY with non-hex content", () => {
    const problems = requiredEnvProblems({ ENCRYPTION_KEY: "z".repeat(64) }); // right length, not hex
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("ENCRYPTION_KEY");
  });

  it("does NOT require INTERNAL_API_SECRET (orphaned config since #180)", () => {
    // Regression guard: enforcing a variable nothing reads would refuse
    // startup over dead config (PR #218 review finding).
    expect(requiredEnvProblems({ ENCRYPTION_KEY: GOOD_KEY })).toEqual([]);
  });
});

describe("assertRequiredEnv", () => {
  it("does not throw for a valid env", () => {
    expect(() => assertRequiredEnv(GOOD_ENV)).not.toThrow();
  });

  it("throws a teaching error that names the var and the fix", () => {
    expect(() => assertRequiredEnv({})).toThrowError(
      /ENCRYPTION_KEY[\s\S]*npm run setup:env/
    );
  });
});
