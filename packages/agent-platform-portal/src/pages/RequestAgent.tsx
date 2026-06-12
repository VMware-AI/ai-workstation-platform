import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";

// "Request Agent" — form that calls C13 (mounted by C1 at
// /admin/approvals/requests via #106). Admin then approves in the
// C2 console (Approvals page, #105) and the orchestrator provisions.

const PACKAGES = [
  "agent-vm-small",
  "agent-vm-medium",
  "agent-vm-large",
  "agent-vm-gpu",
] as const;

function currentUser(): string {
  if (typeof window === "undefined") return "user";
  return window.sessionStorage?.getItem("agent-platform:user") ?? "user";
}

export default function RequestAgent() {
  const [pkg, setPkg] = useState<string>(PACKAGES[0]);
  const [justification, setJustification] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: true; id: number } | { ok: false; msg: string } | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!justification.trim()) {
      setResult({ ok: false, msg: "Justification is required." });
      return;
    }
    setSubmitting(true);
    setResult(null);
    try {
      const created = await api.submitApproval({
        requester: currentUser(),
        package: pkg,
        justification: justification.trim(),
      });
      setResult({ ok: true, id: created.id });
      setJustification("");
    } catch (e: unknown) {
      setResult({ ok: false, msg: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4 max-w-xl">
      <div>
        <h1 className="text-2xl font-semibold">Request Agent</h1>
        <p className="text-sm text-muted-foreground">
          Submit a request — an admin will approve or reject in the C2 console.
        </p>
      </div>
      <Card>
        <CardContent className="p-6">
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Package</label>
              <select
                value={pkg}
                onChange={(e) => setPkg(e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              >
                {PACKAGES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Requester</label>
              <Input value={currentUser()} disabled className="mt-1 font-mono" />
            </div>
            <div>
              <label className="text-sm font-medium">Justification</label>
              <Textarea
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
                placeholder="What will you use it for? (required)"
                rows={4}
                className="mt-1"
              />
            </div>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Submitting…" : "Submit request"}
            </Button>
          </form>

          {result?.ok && (
            <div className="mt-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm">
              Request #{result.id} submitted. An admin will review it shortly.
            </div>
          )}
          {result && !result.ok && (
            <div className="mt-4 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {result.msg}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
