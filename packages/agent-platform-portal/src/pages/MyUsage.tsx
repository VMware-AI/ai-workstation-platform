import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { api, ApiError, type MyUsage as Usage } from "@/lib/api";

// "My Usage" — per-user token consumption summary.
// Backend endpoint /api/me/usage is TBD; until it lands the page
// renders the error state. Chart visualisation is intentionally
// deferred — text rollup first, recharts later.

export default function MyUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .myUsage(30)
      .then((data) => {
        setUsage(data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? e.message : String(e));
        setLoading(false);
      });
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">My Usage</h1>
        <p className="text-sm text-muted-foreground">Token consumption over the last 30 days.</p>
      </div>

      {loading && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">Loading…</CardContent>
        </Card>
      )}
      {error && (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            {error}
            <div className="mt-2 text-xs text-muted-foreground">
              C1 endpoint <code>/api/me/usage</code> is not implemented yet — this view will
              populate once the control plane ships it.
            </div>
          </CardContent>
        </Card>
      )}
      {!loading && !error && usage && (
        <>
          <Card>
            <CardContent className="p-6">
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Total (last 30 days)
              </div>
              <div className="text-3xl font-semibold mt-1">
                {usage.total_tokens.toLocaleString()} tokens
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <th className="py-1">Date</th>
                    <th className="py-1 text-right">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.days.length === 0 ? (
                    <tr>
                      <td className="py-3 text-muted-foreground" colSpan={2}>
                        No usage yet.
                      </td>
                    </tr>
                  ) : (
                    usage.days.map((d) => (
                      <tr key={d.date} className="border-t">
                        <td className="py-1 font-mono text-xs">{d.date}</td>
                        <td className="py-1 text-right">{d.tokens.toLocaleString()}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
