// doc 30 PR-Buf-1 P-4 — pure helpers split out of TopologyChart.tsx so the
// component file stays react-refresh-friendly (no non-component exports).
//
// Tested directly in TopologyChart.test.tsx; consumed by TopologyChart.tsx.

import type { TopologyNode, TopologyResponse } from "@/lib/api";

export const ALL_TENANTS = "__all__";

export function listTenants(nodes: TopologyNode[]): string[] {
  const tenants = new Set<string>();
  for (const n of nodes) {
    if (n.category === "vm" && n.tenant) tenants.add(n.tenant);
  }
  return [...tenants].sort();
}

export function filterByTenant(
  payload: TopologyResponse,
  tenant: string,
): TopologyResponse {
  if (tenant === ALL_TENANTS) return payload;
  const keepIds = new Set<string>();
  const nodes = payload.nodes.filter((n) => {
    const keep = n.category === "vcenter" || n.tenant === tenant;
    if (keep) keepIds.add(n.id);
    return keep;
  });
  const edges = payload.edges.filter(
    (e) => keepIds.has(e.source) && keepIds.has(e.target),
  );
  return { nodes, edges };
}
