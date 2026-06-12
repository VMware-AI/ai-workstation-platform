import { describe, expect, it } from "vitest";
import {
  ALL_TENANTS,
  filterByTenant,
  listTenants,
} from "../topology-helpers";
import type { TopologyResponse } from "@/lib/api";

// We test the pure helpers (listTenants + filterByTenant). The rendered
// echarts component is exercised in Overview.test through DOM smoke; here we
// pin the data-shaping contract that doc 30 PR-Buf-1 P-4 added.

const fixture: TopologyResponse = {
  nodes: [
    { id: "vc-1", name: "vc.example", category: "vcenter", state: "online", tenant: null },
    { id: "vm-1", name: "alice-001", category: "vm", state: "running", tenant: "acme" },
    { id: "vm-2", name: "bob-001", category: "vm", state: "running", tenant: "acme" },
    { id: "vm-3", name: "carol-001", category: "vm", state: "running", tenant: "globex" },
  ],
  edges: [
    { source: "vc-1", target: "vm-1" },
    { source: "vc-1", target: "vm-2" },
    { source: "vc-1", target: "vm-3" },
  ],
};

describe("TopologyChart helpers (P-4 tenant filter)", () => {
  it("lists tenants from VM nodes, alphabetically, vcenter excluded", () => {
    expect(listTenants(fixture.nodes)).toEqual(["acme", "globex"]);
  });

  it("returns the input untouched when filter is All", () => {
    const out = filterByTenant(fixture, ALL_TENANTS);
    expect(out).toBe(fixture);
  });

  it("keeps vcenter centre + VMs in the chosen tenant and drops orphan edges", () => {
    const out = filterByTenant(fixture, "acme");
    const ids = out.nodes.map((n) => n.id).sort();
    expect(ids).toEqual(["vc-1", "vm-1", "vm-2"]);
    expect(out.edges).toEqual([
      { source: "vc-1", target: "vm-1" },
      { source: "vc-1", target: "vm-2" },
    ]);
  });

  it("returns vcenter-only payload when tenant has no VMs", () => {
    const out = filterByTenant(fixture, "nonexistent");
    expect(out.nodes.map((n) => n.id)).toEqual(["vc-1"]);
    expect(out.edges).toEqual([]);
  });
});
