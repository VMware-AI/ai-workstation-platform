import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, ApiError, type VCenterTemplatesResponse } from "@/lib/api";

// Templates sub-view: VM templates for the selected vCenter.
// Backend filter ``list_vms(template=True)`` per vmware-aiops conventions.
export default function VCenterTemplates() {
  const { name } = useParams<{ name: string }>();
  const [data, setData] = useState<VCenterTemplatesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    api
      .vcenterTemplates(name)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [name]);

  if (!name) return <p className="text-sm text-muted-foreground">No vCenter selected.</p>;
  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Failed to load templates</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <code>{error}</code>
        </CardContent>
      </Card>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Templates · {name}</CardTitle>
          <Badge variant="secondary">{data.count}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        {data.templates.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No VM templates found in this vCenter. Mark a VM as a template in vSphere (Action →
            Template → Convert to Template) to surface it here.
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Guest OS</TH>
                <TH>CPU</TH>
                <TH>Memory (GB)</TH>
                <TH>Path</TH>
              </TR>
            </THead>
            <TBody>
              {data.templates.map((t, i) => (
                <TR key={i}>
                  <TD className="font-mono">{String(t.name ?? "—")}</TD>
                  <TD>{String(t.guest_os ?? t.guestOS ?? "—")}</TD>
                  <TD>{String(t.cpu ?? t.num_cpu ?? "—")}</TD>
                  <TD>{String(t.memory_gb ?? t.memory ?? "—")}</TD>
                  <TD className="text-xs text-muted-foreground">{String(t.path ?? "—")}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
