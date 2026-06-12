import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, ApiError, type VCenterInventoryResponse } from "@/lib/api";

// Inventory sub-view: hosts/clusters/datastores/networks for the selected
// vCenter. C1 backs this with a 5-minute in-memory cache, with ?refresh=true
// busting it; UI exposes that as a Refresh button.
export default function VCenterInventory() {
  const { name } = useParams<{ name: string }>();
  const [data, setData] = useState<VCenterInventoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (refresh = false) => {
      if (!name) return;
      setLoading(true);
      setError(null);
      try {
        const r = await api.vcenterInventory(name, refresh);
        setData(r);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [name],
  );

  useEffect(() => {
    load(false);
  }, [load]);

  if (!name) return <p className="text-sm text-muted-foreground">No vCenter selected.</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Inventory · {name}</h2>
          {data && (
            <p className="text-xs text-muted-foreground">
              {data.cached ? (
                <>cached (TTL {data.ttl_s}s)</>
              ) : (
                <>fresh — fetched just now</>
              )}
            </p>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={() => load(true)} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {error && (
        <Card>
          <CardHeader>
            <CardTitle>vCenter unreachable</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <code>{error}</code>
            <p className="mt-2 text-muted-foreground">
              Backend returned a structured 502; check vCenter host/credentials, or try Refresh.
            </p>
          </CardContent>
        </Card>
      )}

      {data && !error && (
        <>
          <KpiRow data={data} />
          <ListCard
            title="Hosts"
            rows={data.hosts}
            columns={["name", "status", "cpu_cores", "memory_gb"]}
          />
          <ListCard title="Clusters" rows={data.clusters} columns={["name", "host_count"]} />
          <ListCard
            title="Datastores"
            rows={data.datastores}
            columns={["name", "type", "capacity_gb", "free_gb"]}
          />
          <ListCard title="Networks" rows={data.networks} columns={["name", "type"]} />
        </>
      )}
    </div>
  );
}

function KpiRow({ data }: { data: VCenterInventoryResponse }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {(["hosts", "clusters", "datastores", "networks"] as const).map((k) => (
        <Card key={k}>
          <CardHeader>
            <CardTitle className="text-xs uppercase text-muted-foreground">{k}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">{data.counts[k]}</div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ListCard({
  title,
  rows,
  columns,
}: {
  title: string;
  rows: Record<string, unknown>[];
  columns: string[];
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{title}</CardTitle>
          <Badge variant="secondary">{rows.length}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No entries.</p>
        ) : (
          <Table>
            <THead>
              <TR>
                {columns.map((c) => (
                  <TH key={c}>{c}</TH>
                ))}
              </TR>
            </THead>
            <TBody>
              {rows.slice(0, 50).map((row, i) => (
                <TR key={i}>
                  {columns.map((c) => (
                    <TD key={c} className="font-mono text-xs">
                      {formatCell(row[c])}
                    </TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        {rows.length > 50 && (
          <p className="mt-2 text-xs text-muted-foreground">
            Showing first 50 of {rows.length} rows.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
