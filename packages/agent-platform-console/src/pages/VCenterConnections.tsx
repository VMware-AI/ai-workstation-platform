import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import {
  api,
  ApiError,
  type VCenterDescriptor,
  type VCenterHealthResponse,
} from "@/lib/api";

// Connections sub-view of the vCenter tab. M1 lists a single configured
// vCenter sourced from env (decision 4-vCenter revision); multi-vCenter
// via config.yaml lands in M2. Health probe is on-demand per row.
type HealthState = Record<string, VCenterHealthResponse | "loading" | { error: string }>;

function statusBadge(status: VCenterHealthResponse["status"] | undefined) {
  if (status === "ok") return <Badge variant="success">healthy</Badge>;
  if (status === "timeout") return <Badge variant="warning">timeout</Badge>;
  if (status === "error") return <Badge variant="destructive">error</Badge>;
  return <Badge variant="secondary">unprobed</Badge>;
}

export default function VCenterConnections() {
  const [list, setList] = useState<VCenterDescriptor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [healths, setHealths] = useState<HealthState>({});

  useEffect(() => {
    api
      .listVCenters()
      .then((r) => setList(r.vcenters))
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  const probe = useCallback(async (name: string) => {
    setHealths((h) => ({ ...h, [name]: "loading" }));
    try {
      const result = await api.vcenterHealth(name);
      setHealths((h) => ({ ...h, [name]: result }));
    } catch (e) {
      setHealths((h) => ({
        ...h,
        [name]: { error: e instanceof ApiError ? e.message : String(e) },
      }));
    }
  }, []);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Failed to load vCenters</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <code>{error}</code>
        </CardContent>
      </Card>
    );
  }

  if (list === null) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (list.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No vCenter configured</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Set <code>AGENT_PLATFORM_VCENTER_HOST</code> (and <code>USER</code>/<code>PASSWORD</code>) on the
          control plane to enable inventory views. Multi-vCenter via config.yaml lands in M2.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Connections</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Host</TH>
                <TH>User</TH>
                <TH>TLS verify</TH>
                <TH>Health</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {list.map((vc) => {
                const h = healths[vc.name];
                const probed = typeof h === "object" && h !== null && "status" in h ? h : undefined;
                const errored = typeof h === "object" && h !== null && "error" in h ? h : undefined;
                return (
                  <TR key={vc.name}>
                    <TD className="font-mono">{vc.name}</TD>
                    <TD>{vc.host}</TD>
                    <TD>{vc.user || <span className="text-muted-foreground">—</span>}</TD>
                    <TD>{vc.verify_ssl ? "on" : "off"}</TD>
                    <TD>
                      {h === "loading"
                        ? <Badge variant="secondary">probing…</Badge>
                        : errored
                        ? <Badge variant="destructive">{errored.error}</Badge>
                        : statusBadge(probed?.status)}
                    </TD>
                    <TD className="space-x-2">
                      <Button size="sm" variant="outline" onClick={() => probe(vc.name)}>
                        Probe
                      </Button>
                      <Link
                        to={`/vcenter/${encodeURIComponent(vc.name)}/inventory`}
                        className="text-sm text-primary underline-offset-2 hover:underline"
                      >
                        Inventory →
                      </Link>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
