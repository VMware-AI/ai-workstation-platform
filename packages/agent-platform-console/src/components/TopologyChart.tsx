import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type TopologyResponse, type TopologyNode } from "@/lib/api";
import { ALL_TENANTS, filterByTenant, listTenants } from "./topology-helpers";

/**
 * W-2: circular topology chart for the Overview tab.
 *
 * vCenter sits in the center; each VM is a leaf attached by one edge. Node
 * colour reflects VM state. Uses echarts force layout with low repulsion so
 * the layout settles into a ring around the vCenter rather than scattering.
 *
 * doc 30 PR-Buf-1 polish (2026-06-01) — P-4 tenant filter:
 *   - Tenants are derived from the payload (vcenter node carries no tenant);
 *     dropdown defaults to "All tenants".
 *   - When a tenant is selected we keep the vcenter centre node, drop VM
 *     nodes outside the tenant, and drop edges that reference dropped nodes.
 */

const STATE_COLOR: Record<string, string> = {
  running: "#16a34a",
  provisioning: "#f59e0b",
  stopping: "#a3a3a3",
  stopped: "#a3a3a3",
  retired: "#737373",
  error: "#dc2626",
  online: "#0ea5e9", // vCenter center
};

function nodeColor(node: TopologyNode): string {
  return STATE_COLOR[node.state] ?? "#94a3b8";
}

function buildOption(payload: TopologyResponse): EChartsOption {
  return {
    tooltip: {
      formatter: (params: unknown) => {
        const data = (params as { data?: TopologyNode }).data;
        if (!data || !("category" in data)) return "";
        if (data.category === "vcenter") return `vCenter: ${data.name}`;
        return `VM: ${data.name}<br/>state: ${data.state}<br/>tenant: ${data.tenant ?? "—"}`;
      },
    },
    legend: [
      {
        data: ["vcenter", "vm"],
        bottom: 0,
      },
    ],
    series: [
      {
        type: "graph",
        layout: "force",
        symbolSize: (_v: unknown, params: unknown) => {
          const data = (params as { data?: TopologyNode }).data;
          return data?.category === "vcenter" ? 50 : 30;
        },
        roam: true,
        label: { show: true, position: "right" },
        force: {
          repulsion: 200,
          edgeLength: 120,
          gravity: 0.1,
        },
        categories: [{ name: "vcenter" }, { name: "vm" }],
        data: payload.nodes.map((n) => ({
          id: n.id,
          name: n.name,
          category: n.category === "vcenter" ? 0 : 1,
          itemStyle: { color: nodeColor(n) },
          // attach original node so the tooltip can read state + tenant
          state: n.state,
          tenant: n.tenant,
        })),
        edges: payload.edges.map((e) => ({ source: e.source, target: e.target })),
      },
    ],
  };
}

export function TopologyChart() {
  const [state, setState] = useState<{
    data?: TopologyResponse;
    error?: string;
    loading: boolean;
  }>({ loading: true });
  const [tenant, setTenant] = useState<string>(ALL_TENANTS);

  useEffect(() => {
    let cancelled = false;
    api
      .getVmsTopology()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false });
      })
      .catch((e) => {
        if (!cancelled) setState({ error: String(e), loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tenants = useMemo(
    () => (state.data ? listTenants(state.data.nodes) : []),
    [state.data],
  );

  const filtered = useMemo(
    () => (state.data ? filterByTenant(state.data, tenant) : undefined),
    [state.data, tenant],
  );

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0">
        <CardTitle className="text-sm">Topology</CardTitle>
        {tenants.length > 0 && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>Tenant</span>
            <select
              data-testid="tenant-filter"
              className="rounded-md border border-input bg-background px-2 py-1 text-xs"
              value={tenant}
              onChange={(e) => setTenant(e.target.value)}
            >
              <option value={ALL_TENANTS}>All tenants</option>
              {tenants.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        )}
      </CardHeader>
      <CardContent>
        {state.loading && (
          <div className="py-12 text-center text-sm text-muted-foreground">Loading topology…</div>
        )}
        {state.error && <div className="py-12 text-center text-sm text-destructive">{state.error}</div>}
        {filtered && filtered.nodes.length > 0 && (
          <ReactECharts
            option={buildOption(filtered)}
            style={{ height: 360 }}
            notMerge
            data-testid="topology-chart"
          />
        )}
        {filtered && filtered.nodes.length === 0 && (
          <div className="py-12 text-center text-sm text-muted-foreground">
            No topology data yet.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

