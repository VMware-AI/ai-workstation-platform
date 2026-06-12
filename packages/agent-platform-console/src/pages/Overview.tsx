import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TopologyChart } from "@/components/TopologyChart";
import { api, type Health, type Version } from "@/lib/api";

// Replacement for the old薄 Dashboard.tsx (deleted in R-4). For R-1 only the
// control-plane card has real data; the other 5 cards render placeholder values
// waiting for /admin/overview to land in R-2.
//
// Per docs/architecture/21 §1.2 "Overview", the eventual layout is:
//   row 1: control-plane · vCenter · runtime
//   row 2: VMs running · approvals pending · token spend today
//   row 3: recent events (last 50)

type Probe = { healthy: boolean | null; version: Version | null };

export default function Overview() {
  const [probe, setProbe] = useState<Probe>({ healthy: null, version: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [h, v] = await Promise.all([
          api.healthz().catch<Health>(() => ({ status: "error" })),
          api.version().catch<Version>(() => ({ version: "unknown" })),
        ]);
        if (!cancelled) setProbe({ healthy: h.status === "ok", version: v });
      } catch {
        if (!cancelled) setProbe({ healthy: false, version: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Platform-wide snapshot. Aggregated <code>/admin/overview</code> lands with R-2; for now
          only control-plane probe is live.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Control plane</CardTitle>
          </CardHeader>
          <CardContent>
            {probe.healthy === null ? (
              <Badge variant="secondary">checking…</Badge>
            ) : probe.healthy ? (
              <Badge variant="success">healthy</Badge>
            ) : (
              <Badge variant="destructive">unreachable</Badge>
            )}
            <div className="mt-2 font-mono text-xs text-muted-foreground">
              {probe.version?.version ?? "—"}
            </div>
          </CardContent>
        </Card>
        <OverviewKpiCard title="vCenter" pendingLabel="awaiting R-2 + R-3" />
        <OverviewKpiCard title="Runtime" pendingLabel="awaiting R-2" />
        <OverviewKpiCard title="VMs running" pendingLabel="awaiting R-2" />
        <OverviewKpiCard title="Approvals pending" pendingLabel="awaiting R-2" />
        <OverviewKpiCard title="Token spend today" pendingLabel="awaiting R-2" />
      </div>

      <TopologyChart />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Recent events</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <code>/admin/events?since=&amp;limit=</code> lands with R-2. This panel will stream the
          last 50 audit-log entries.
        </CardContent>
      </Card>
    </div>
  );
}

function OverviewKpiCard({ title, pendingLabel }: { title: string; pendingLabel: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <Badge variant="secondary">{pendingLabel}</Badge>
      </CardContent>
    </Card>
  );
}
