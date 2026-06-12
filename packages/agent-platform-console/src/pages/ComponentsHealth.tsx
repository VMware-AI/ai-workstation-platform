import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, ApiError, type ComponentRow } from "@/lib/api";

// Operations → Components Health: live HTTP services + static labels for
// libraries/CLIs/placeholders (decision 4.3 revised). Only C5 + C1-self
// are live HTTP probes in M1; everything else surfaces as not_a_service.
function statusBadge(row: ComponentRow) {
  switch (row.status) {
    case "ok":
      return <Badge variant="success">healthy</Badge>;
    case "unreachable":
      return <Badge variant="destructive">unreachable</Badge>;
    case "timeout":
      return <Badge variant="warning">timeout</Badge>;
    case "unknown":
      return <Badge variant="secondary">unknown</Badge>;
    case "not_a_service":
      return <Badge variant="outline">{row.kind}</Badge>;
    default:
      return <Badge variant="destructive">{row.status}</Badge>;
  }
}

export default function ComponentsHealth() {
  const [rows, setRows] = useState<ComponentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api
        .componentsHealth()
        .then((r) => {
          if (!cancelled) setRows(r.components);
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
        });
    };
    load();
    const id = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Components</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          M1 probes only HTTP services (C1, C5). Library and CLI components surface as static
          labels — see <code>docs/architecture/21 §4.3</code> for the rationale.
        </CardContent>
      </Card>

      {error && (
        <Card>
          <CardContent className="pt-4 text-sm">
            <code>{error}</code>
          </CardContent>
        </Card>
      )}

      {rows && (
        <Card>
          <CardContent className="pt-4">
            <Table>
              <THead>
                <TR>
                  <TH>ID</TH>
                  <TH>Label</TH>
                  <TH>Kind</TH>
                  <TH>Status</TH>
                  <TH>Version</TH>
                  <TH>Notes</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((r) => (
                  <TR key={r.id}>
                    <TD className="font-mono">{r.id}</TD>
                    <TD>{r.label}</TD>
                    <TD className="text-xs uppercase text-muted-foreground">{r.kind}</TD>
                    <TD>{statusBadge(r)}</TD>
                    <TD className="font-mono text-xs">{r.version ?? "—"}</TD>
                    <TD className="text-xs text-muted-foreground">
                      {r.error || r.note || r.probed || "—"}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
