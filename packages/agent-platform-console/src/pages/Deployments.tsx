import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { ApiError } from "@/lib/api";

// Deployments list. C1's /v1/deployments is admin-gated and paginates by
// returning a flat list (server-side `limit` defaults to 50 in C1). This
// page reads it directly with the typed helper below.
type DeploymentRow = {
  id: string;
  tenant_id: string;
  template: string;
  image_version: string;
  state: string;
  counts: { requested: number; succeeded: number; failed: number };
  created_at: string;
  updated_at: string;
};

async function listDeployments(): Promise<DeploymentRow[]> {
  const base = (import.meta.env.VITE_CONTROL_BASE_URL ?? "").replace(/\/$/, "");
  const r = await fetch(`${base}/v1/deployments`, { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, `/v1/deployments → ${r.status} ${r.statusText}`);
  return (await r.json()) as DeploymentRow[];
}

function stateBadge(s: string) {
  if (s === "completed") return <Badge variant="success">{s}</Badge>;
  if (s === "provisioning" || s === "cloning") return <Badge variant="warning">{s}</Badge>;
  if (s === "failed" || s === "partially_failed") return <Badge variant="destructive">{s}</Badge>;
  return <Badge variant="secondary">{s}</Badge>;
}

export default function Deployments() {
  const [rows, setRows] = useState<DeploymentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDeployments()
      .then(setRows)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Failed to load deployments</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <code>{error}</code>
        </CardContent>
      </Card>
    );
  }

  if (rows === null) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Deployments</CardTitle>
          <Badge variant="secondary">{rows.length}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No deployments yet. New deployments arrive via{" "}
            <code>POST /v1/deployments</code> or the approval flow.
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>ID</TH>
                <TH>Tenant</TH>
                <TH>Template</TH>
                <TH>Image</TH>
                <TH>State</TH>
                <TH>Counts (✓ / ✗ / total)</TH>
                <TH>Updated</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.id}>
                  <TD>
                    <Link
                      to={`/lifecycle/deployments/${encodeURIComponent(r.id)}`}
                      className="font-mono text-primary underline-offset-2 hover:underline"
                    >
                      {r.id.slice(0, 8)}…
                    </Link>
                  </TD>
                  <TD className="font-mono text-xs">{r.tenant_id}</TD>
                  <TD className="text-xs">{r.template}</TD>
                  <TD className="font-mono text-xs">{r.image_version}</TD>
                  <TD>{stateBadge(r.state)}</TD>
                  <TD className="font-mono text-xs">
                    {r.counts.succeeded} / {r.counts.failed} / {r.counts.requested}
                  </TD>
                  <TD className="text-xs text-muted-foreground">
                    {new Date(r.updated_at).toLocaleString()}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
