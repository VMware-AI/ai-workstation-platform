import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { BatchCreateDrawer } from "@/components/BatchCreateDrawer";
import { api, type VmListResponse, type VmRow } from "@/lib/api";

const STATE_VARIANT: Record<VmRow["state"], "success" | "secondary" | "warning" | "destructive"> =
  {
    running: "success",
    stopped: "secondary",
    creating: "warning",
    error: "destructive",
  };

export default function VMs() {
  const [state, setState] = useState<{ data?: VmListResponse; error?: string; loading: boolean }>({
    loading: true,
  });
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .listVms()
      .then((data) => setState({ data, loading: false }))
      .catch((e) => setState({ error: String(e), loading: false }));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">VMs</h1>
          <p className="text-sm text-muted-foreground">
            All tenant agent VMs across the platform.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {state.data?._stub && <Badge variant="warning">backend stub</Badge>}
          <Button onClick={() => setDrawerOpen(true)}>Batch Create</Button>
        </div>
      </div>
      <Card>
        {state.loading && <div className="p-6 text-sm text-muted-foreground">Loading…</div>}
        {state.error && <div className="p-6 text-sm text-destructive">{state.error}</div>}
        {state.data && (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Tenant</TH>
                <TH>State</TH>
                <TH>IP</TH>
              </TR>
            </THead>
            <TBody>
              {state.data.vms.length === 0 ? (
                <TR>
                  <TD colSpan={4} className="py-6 text-center text-muted-foreground">
                    No VMs yet — submit an agent request via the portal.
                  </TD>
                </TR>
              ) : (
                state.data.vms.map((vm) => (
                  <TR key={vm.id}>
                    <TD className="font-medium">{vm.name}</TD>
                    <TD>{vm.tenant}</TD>
                    <TD>
                      <Badge variant={STATE_VARIANT[vm.state]}>{vm.state}</Badge>
                    </TD>
                    <TD className="font-mono text-xs">{vm.ip ?? "—"}</TD>
                  </TR>
                ))
              )}
            </TBody>
          </Table>
        )}
      </Card>
      <BatchCreateDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onCreated={(id) => navigate(`/lifecycle/deployments/${id}`)}
      />
    </div>
  );
}
