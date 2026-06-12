import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, type AuditListResponse } from "@/lib/api";

export default function Audit() {
  const [state, setState] = useState<{
    data?: AuditListResponse;
    error?: string;
    loading: boolean;
  }>({ loading: true });

  useEffect(() => {
    api
      .listAudit(100)
      .then((data) => setState({ data, loading: false }))
      .catch((e) => setState({ error: String(e), loading: false }));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Audit</h1>
          <p className="text-sm text-muted-foreground">
            Every state-changing call recorded by the control plane.
          </p>
        </div>
        {state.data?._stub && <Badge variant="warning">backend stub</Badge>}
      </div>
      <Card>
        {state.loading && <div className="p-6 text-sm text-muted-foreground">Loading…</div>}
        {state.error && <div className="p-6 text-sm text-destructive">{state.error}</div>}
        {state.data && (
          <Table>
            <THead>
              <TR>
                <TH>Time</TH>
                <TH>Actor</TH>
                <TH>Action</TH>
                <TH>Target</TH>
              </TR>
            </THead>
            <TBody>
              {state.data.entries.length === 0 ? (
                <TR>
                  <TD colSpan={4} className="py-6 text-center text-muted-foreground">
                    No audit entries yet.
                  </TD>
                </TR>
              ) : (
                state.data.entries.map((e) => (
                  <TR key={e.id}>
                    <TD className="font-mono text-xs">{e.at}</TD>
                    <TD>{e.actor}</TD>
                    <TD className="font-mono text-xs">{e.action}</TD>
                    <TD>{e.target}</TD>
                  </TR>
                ))
              )}
            </TBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
