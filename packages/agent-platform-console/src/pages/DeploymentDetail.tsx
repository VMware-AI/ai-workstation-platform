import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { ApiError } from "@/lib/api";

// Single-deployment detail per docs/architecture/21 §4.5 decision B:
// metadata + items + heartbeat + cloud-init log retrieval.
type DeploymentItem = {
  id: number;
  owner_id: string;
  intended_name: string;
  state: string;
  vm_id: string | null;
  error_message: string | null;
  attempts: number;
};

type DeploymentDetailResponse = {
  id: string;
  tenant_id: string;
  template: string;
  image_version: string;
  state: string;
  counts: { requested: number; succeeded: number; failed: number };
  items: DeploymentItem[];
  created_at: string;
  updated_at: string;
};

type CloudInitLogResponse = {
  available: boolean;
  reason?: string;
  expected_path?: string;
  log?: string;
  bytes?: number;
  truncated?: boolean;
  path?: string;
};

async function getDeployment(id: string): Promise<DeploymentDetailResponse> {
  const base = (import.meta.env.VITE_CONTROL_BASE_URL ?? "").replace(/\/$/, "");
  const r = await fetch(`${base}/v1/deployments/${encodeURIComponent(id)}`, {
    credentials: "include",
  });
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${r.statusText}`);
  return (await r.json()) as DeploymentDetailResponse;
}

async function getCloudInitLog(
  deploymentId: string,
  itemId: number,
): Promise<CloudInitLogResponse> {
  const base = (import.meta.env.VITE_CONTROL_BASE_URL ?? "").replace(/\/$/, "");
  const r = await fetch(
    `${base}/v1/deployments/${encodeURIComponent(deploymentId)}/items/${itemId}/cloud-init-log`,
    { credentials: "include" },
  );
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${r.statusText}`);
  return (await r.json()) as CloudInitLogResponse;
}

function itemStateBadge(s: string) {
  if (s === "powered_on" || s === "succeeded") return <Badge variant="success">{s}</Badge>;
  if (s === "cloning" || s === "customizing" || s === "pending")
    return <Badge variant="warning">{s}</Badge>;
  if (s === "failed" || s === "cancelled") return <Badge variant="destructive">{s}</Badge>;
  return <Badge variant="secondary">{s}</Badge>;
}

export default function DeploymentDetail() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<DeploymentDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openLog, setOpenLog] = useState<number | null>(null);
  const [logs, setLogs] = useState<Record<number, CloudInitLogResponse | "loading">>({});

  useEffect(() => {
    if (!id) return;
    getDeployment(id)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [id]);

  async function toggleLog(itemId: number) {
    if (openLog === itemId) {
      setOpenLog(null);
      return;
    }
    setOpenLog(itemId);
    if (!logs[itemId]) {
      setLogs((l) => ({ ...l, [itemId]: "loading" }));
      try {
        const r = await getCloudInitLog(id!, itemId);
        setLogs((l) => ({ ...l, [itemId]: r }));
      } catch (e) {
        setLogs((l) => ({
          ...l,
          [itemId]: {
            available: false,
            reason: e instanceof ApiError ? e.message : String(e),
          },
        }));
      }
    }
  }

  if (!id) return <p className="text-sm text-muted-foreground">No deployment selected.</p>;
  if (error)
    return (
      <Card>
        <CardHeader>
          <CardTitle>Deployment not found</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <code>{error}</code>
          <p className="mt-2">
            <Link to="/lifecycle/deployments" className="text-primary underline-offset-2 hover:underline">
              ← back to list
            </Link>
          </p>
        </CardContent>
      </Card>
    );
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <div>
          <h2 className="font-mono text-lg font-semibold">{data.id}</h2>
          <p className="text-xs text-muted-foreground">
            <Link
              to="/lifecycle/deployments"
              className="text-primary underline-offset-2 hover:underline"
            >
              ← back to list
            </Link>
          </p>
        </div>
        <Badge variant="outline">{data.state}</Badge>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetaCard label="Tenant" value={data.tenant_id} mono />
        <MetaCard label="Template" value={data.template} />
        <MetaCard label="Image" value={data.image_version} mono />
        <MetaCard
          label="Counts (✓/✗/all)"
          value={`${data.counts.succeeded} / ${data.counts.failed} / ${data.counts.requested}`}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Items</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>ID</TH>
                <TH>Owner</TH>
                <TH>Intended name</TH>
                <TH>State</TH>
                <TH>VM ID</TH>
                <TH>Attempts</TH>
                <TH>cloud-init</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((it) => (
                <Fragment key={it.id}>
                  <TR>
                    <TD className="font-mono text-xs">{it.id}</TD>
                    <TD>{it.owner_id}</TD>
                    <TD className="font-mono text-xs">{it.intended_name}</TD>
                    <TD>{itemStateBadge(it.state)}</TD>
                    <TD className="font-mono text-xs">{it.vm_id ?? "—"}</TD>
                    <TD>{it.attempts}</TD>
                    <TD>
                      <Button size="sm" variant="outline" onClick={() => toggleLog(it.id)}>
                        {openLog === it.id ? "Hide log" : "Show log"}
                      </Button>
                    </TD>
                  </TR>
                  {openLog === it.id && (
                    <TR>
                      <TD colSpan={7}>
                        <LogPanel state={logs[it.id]} />
                        {it.error_message && (
                          <p className="mt-2 text-xs text-destructive">
                            Error: <code>{it.error_message}</code>
                          </p>
                        )}
                      </TD>
                    </TR>
                  )}
                </Fragment>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function MetaCard({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs uppercase text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent className={mono ? "font-mono text-sm" : "text-sm"}>{value}</CardContent>
    </Card>
  );
}

function LogPanel({ state }: { state: CloudInitLogResponse | "loading" | undefined }) {
  if (state === "loading") {
    return <p className="text-xs text-muted-foreground">Loading…</p>;
  }
  if (!state) return null;
  if (!state.available) {
    return (
      <div className="rounded-md border border-dashed bg-muted/30 p-3 text-xs">
        <p className="text-muted-foreground">{state.reason ?? "Log not available."}</p>
        {state.expected_path && (
          <p className="mt-1 font-mono text-muted-foreground">
            expected: {state.expected_path}
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span>{state.bytes} bytes</span>
        {state.truncated && <Badge variant="secondary">tail (8 KB)</Badge>}
        {state.path && <span className="font-mono">{state.path}</span>}
      </div>
      <pre className="max-h-96 overflow-auto rounded bg-muted/60 p-3 text-xs">
        <code>{state.log}</code>
      </pre>
    </div>
  );
}
