import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

// Tenant-scope guard (#90 M6). withTenant authenticates but does NOT inject
// a tenant filter into Prisma queries — every route adds
// `where: { tenantId: session.tenantId }` by hand. The convention held at
// 24/24 call sites when this guard landed; this test makes forgetting it a
// CI failure instead of a cross-tenant data leak.
//
// Rule: inside src/app/api, every prisma call on a tenant-scoped model must
// either contain "tenantId" in its argument, or carry an explicit
// `// tenant-scope:` comment within the 5 lines above it explaining why the
// call is legitimately tenant-independent (e.g. bearer-token-authenticated
// agent endpoints) or already ownership-checked.

const SCOPED_MODELS = [
  "instance",
  "computePool",
  "modelProvider",
  "usageRecord",
  "billingRecord",
  "session",
  "membership",
];

const API_ROOT = join(__dirname, "..", "..", "app", "api");

function routeFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) {
      if (entry === "__tests__") continue;
      out.push(...routeFiles(p));
    } else if (entry.endsWith(".ts")) {
      out.push(p);
    }
  }
  return out;
}

/** Extract the balanced-paren argument text of a call starting at `open`. */
function callArg(src: string, open: number): string {
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "(") depth++;
    else if (src[i] === ")") {
      depth--;
      if (depth === 0) return src.slice(open, i + 1);
    }
  }
  return src.slice(open);
}

describe("tenant-scope guard (#90 M6)", () => {
  it("every prisma call on a tenant-scoped model is tenant-filtered or explicitly exempted", () => {
    const violations: string[] = [];
    const callRe = new RegExp(`prisma\\.(${SCOPED_MODELS.join("|")})\\.\\w+\\s*\\(`, "g");

    for (const file of routeFiles(API_ROOT)) {
      const src = readFileSync(file, "utf8");
      for (const m of src.matchAll(callRe)) {
        const idx = m.index ?? 0;
        const arg = callArg(src, idx + m[0].length - 1);
        if (arg.includes("tenantId")) continue;
        // Exemption marker within the 5 lines above the call.
        const before = src.slice(0, idx);
        const recent = before.split("\n").slice(-6).join("\n");
        if (recent.includes("tenant-scope:")) continue;
        const line = before.split("\n").length;
        violations.push(`${file.slice(file.indexOf("src/")).replace(/\\/g, "/")}:${line} — ${m[0]}…)`);
      }
    }

    expect(violations, `unscoped prisma calls (add tenantId or a "// tenant-scope: <why>" comment):\n${violations.join("\n")}`).toEqual([]);
  });
});
