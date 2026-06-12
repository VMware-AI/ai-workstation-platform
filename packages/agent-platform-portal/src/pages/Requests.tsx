import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { RequestTimeline } from "@/components/RequestTimeline";
import { api, ApiError, type MyApproval } from "@/lib/api";

// W-4: "My Requests" page. Lists every approval row submitted by the
// current user (sessionStorage identity, replaced by Keycloak in M2.1),
// each rendered as a card with a 4-step timeline.
//
// Backend wiring: hits /admin/approvals/requests?requester=<user>, which
// is the same endpoint the admin C2 console uses but filtered to the
// caller. The filter was verified in T-25.1 (agent_platform_approval/http.py:128).

function currentUser(): string {
  if (typeof window === "undefined") return "user";
  return window.sessionStorage?.getItem("agent-platform:user") ?? "user";
}

function stateBadge(s: MyApproval["state"]) {
  if (s === "approved") return <Badge variant="success">approved</Badge>;
  if (s === "rejected") return <Badge variant="destructive">rejected</Badge>;
  return <Badge variant="warning">pending</Badge>;
}

export default function Requests() {
  const user = currentUser();
  const [rows, setRows] = useState<MyApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listMyApprovals(user)
      .then((data) => {
        setRows(data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? e.message : String(e));
        setLoading(false);
      });
  }, [user]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">My Requests</h1>
        <p className="text-sm text-muted-foreground">
          Approval queue for VMs you have requested. Pending and decided requests live here;
          provisioning + ready states light up once C1 ships the rest of the deployment
          state machine.
        </p>
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
              Backend endpoint <code>/admin/approvals/requests?requester={user}</code> failed —
              check that C1 has the C13 approval router mounted.
            </div>
          </CardContent>
        </Card>
      )}
      {!loading && !error && rows.length === 0 && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            No requests yet. Head over to{" "}
            <a href="/request" className="underline">
              Request Agent
            </a>{" "}
            to submit one.
          </CardContent>
        </Card>
      )}
      {!loading &&
        !error &&
        rows.map((r) => (
          <Card key={r.id}>
            <CardContent className="space-y-2 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">#{r.id}</span>
                    <span className="font-mono text-xs">{r.package}</span>
                    {stateBadge(r.state)}
                  </div>
                  <div className="text-xs text-muted-foreground">{r.justification}</div>
                </div>
                {r.decision_reason && (
                  <div className="max-w-xs text-xs italic text-muted-foreground">
                    “{r.decision_reason}”
                  </div>
                )}
              </div>
              <RequestTimeline approval={r} />
            </CardContent>
          </Card>
        ))}
    </div>
  );
}
