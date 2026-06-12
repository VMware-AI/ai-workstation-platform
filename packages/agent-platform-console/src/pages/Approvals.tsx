import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import {
  api,
  ApiError,
  type ApprovalRequest,
  type ApprovalState,
} from "@/lib/api";

// Console assumes C1 mounts the C13 router at /admin/approvals.
// Pending requests get the default filter; admins flip the toggle to
// browse decided history. Decisions are stamped with the operator's
// short name pulled from session storage (filled by the auth shim;
// falls back to "console" for local dev).

const FILTERS: { label: string; state?: ApprovalState }[] = [
  { label: "Pending", state: "pending" },
  { label: "Approved", state: "approved" },
  { label: "Rejected", state: "rejected" },
  { label: "All", state: undefined },
];

function stateBadge(state: ApprovalState) {
  if (state === "approved") return <Badge variant="success">approved</Badge>;
  if (state === "rejected") return <Badge variant="destructive">rejected</Badge>;
  return <Badge variant="warning">pending</Badge>;
}

function currentAdmin(): string {
  if (typeof window === "undefined") return "console";
  return window.sessionStorage?.getItem("agent-platform:admin") ?? "console";
}

export default function Approvals() {
  const [filter, setFilter] = useState<ApprovalState | undefined>("pending");
  const [rows, setRows] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ApprovalRequest | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Bumping the token re-runs the effect below (used after approve/reject).
  const [reloadToken, setReloadToken] = useState(0);
  const reload = useCallback(() => setReloadToken((t) => t + 1), []);

  // Cancelled-flag guard (same pattern as TokenUsage.tsx): rapid filter
  // switches abandon in-flight responses so a slow earlier request can't
  // overwrite the rows of the currently selected filter.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .listApprovals({ state: filter, limit: 100 })
      .then((data) => {
        if (cancelled) return;
        setRows(data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? e.message : String(e);
        setError(msg);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filter, reloadToken]);

  async function decide(target: "approve" | "reject") {
    if (!selected) return;
    setActionError(null);
    const admin = currentAdmin();
    try {
      if (target === "approve") {
        await api.approveRequest(selected.id, { admin });
      } else {
        const reason = window.prompt("Reject reason (required):") ?? "";
        if (!reason.trim()) {
          setActionError("Reject reason cannot be empty.");
          return;
        }
        await api.rejectRequest(selected.id, { admin, reason: reason.trim() });
      }
      setSelected(null);
      reload();
    } catch (e: unknown) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Approvals</h1>
          <p className="text-sm text-muted-foreground">
            Review user-submitted requests from C13. Approve or reject — both are terminal.
          </p>
        </div>
        <div className="flex gap-2">
          {FILTERS.map((f) => (
            <Button
              key={f.label}
              size="sm"
              variant={filter === f.state ? "default" : "outline"}
              onClick={() => setFilter(f.state)}
            >
              {f.label}
            </Button>
          ))}
        </div>
      </div>

      <Card>
        {loading && <div className="p-6 text-sm text-muted-foreground">Loading…</div>}
        {error && (
          <div className="p-6 text-sm text-destructive">
            {error}
            <div className="mt-2 text-xs text-muted-foreground">
              C13 router may not be mounted yet — verify <code>/admin/approvals/requests</code> on
              the control plane.
            </div>
          </div>
        )}
        {!loading && !error && (
          <Table>
            <THead>
              <TR>
                <TH>ID</TH>
                <TH>Requester</TH>
                <TH>Package</TH>
                <TH>State</TH>
                <TH>Created</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {rows.length === 0 ? (
                <TR>
                  <TD colSpan={6} className="py-6 text-center text-muted-foreground">
                    No requests in this view.
                  </TD>
                </TR>
              ) : (
                rows.map((r) => (
                  <TR key={r.id}>
                    <TD className="font-mono text-xs">{r.id}</TD>
                    <TD>{r.requester}</TD>
                    <TD className="font-mono text-xs">{r.package}</TD>
                    <TD>{stateBadge(r.state)}</TD>
                    <TD className="font-mono text-xs">{r.created_at}</TD>
                    <TD>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          api
                            .getApproval(r.id)
                            .then((full) => setSelected(full))
                            .catch((e: unknown) =>
                              setActionError(e instanceof ApiError ? e.message : String(e)),
                            )
                        }
                      >
                        Open
                      </Button>
                    </TD>
                  </TR>
                ))
              )}
            </TBody>
          </Table>
        )}
      </Card>

      {selected && (
        <Card>
          <div className="p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-muted-foreground">
                  Request #{selected.id} · {stateBadge(selected.state)}
                </div>
                <div className="text-lg font-semibold">
                  {selected.requester} → {selected.package}
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
                Close
              </Button>
            </div>

            <div className="text-sm whitespace-pre-wrap">{selected.justification}</div>

            {selected.state === "pending" && (
              <div className="flex gap-2">
                <Button onClick={() => decide("approve")}>Approve</Button>
                <Button variant="destructive" onClick={() => decide("reject")}>
                  Reject
                </Button>
              </div>
            )}

            {actionError && <div className="text-sm text-destructive">{actionError}</div>}

            <div>
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                Audit history
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>When</TH>
                    <TH>Actor</TH>
                    <TH>Event</TH>
                  </TR>
                </THead>
                <TBody>
                  {selected.audit_events.map((e) => (
                    <TR key={e.id}>
                      <TD className="font-mono text-xs">{e.at}</TD>
                      <TD>{e.actor}</TD>
                      <TD className="font-mono text-xs">{e.event_type}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
