import { describe, expect, it } from "vitest";
import { provisionViaGovc } from "../govc-provision";
import { GovcError, type GovcRunner } from "../govc";

// Fake govc that returns canned output per subcommand and records the call
// order, with optional per-subcommand failures to exercise rollback.
function fakeGovc(failOn?: string): { run: GovcRunner; subcommands: () => string[] } {
  const calls: string[][] = [];
  const run: GovcRunner = async (args) => {
    calls.push(args);
    if (failOn && args[0] === failOn) throw new GovcError(`boom ${failOn}`);
    if (args[0] === "vm.ip") return "10.0.1.7\n";
    return "";
  };
  return { run, subcommands: () => calls.map((c) => c[0]) };
}

const spec = { templateName: "tmpl", name: "web-01" };
const docs = { userdata: "USER", metadata: "META" };

describe("provisionViaGovc", () => {
  it("runs clone → inject → power → wait and returns the IP (no rollback)", async () => {
    const { run, subcommands } = fakeGovc();
    const result = await provisionViaGovc(run, spec, docs, { waitIpSeconds: 60 });
    expect(result).toEqual({ vmName: "web-01", ip: "10.0.1.7" });
    expect(subcommands()).toEqual(["vm.clone", "vm.change", "vm.power", "vm.ip"]);
    expect(subcommands()).not.toContain("vm.destroy");
  });

  it("rolls back (vm.destroy) when guestinfo injection fails", async () => {
    const { run, subcommands } = fakeGovc("vm.change");
    await expect(provisionViaGovc(run, spec, docs)).rejects.toThrow(/boom vm.change/);
    expect(subcommands()).toEqual(["vm.clone", "vm.change", "vm.destroy"]);
  });

  it("rolls back when power-on fails", async () => {
    const { run, subcommands } = fakeGovc("vm.power");
    await expect(provisionViaGovc(run, spec, docs)).rejects.toThrow(GovcError);
    expect(subcommands()).toEqual(["vm.clone", "vm.change", "vm.power", "vm.destroy"]);
  });

  it("wraps a non-GovcError failure as GovcError", async () => {
    const run: GovcRunner = async (args) => {
      if (args[0] === "vm.ip") throw new Error("plain");
      return "";
    };
    await expect(provisionViaGovc(run, spec, docs)).rejects.toThrow(GovcError);
  });
});
