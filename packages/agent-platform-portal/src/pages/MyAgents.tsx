import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api, ApiError, type MyAgent, type MyUsage } from "@/lib/api";

// "My Agents" — list of VMs provisioned for the current user.
// Backend endpoint /api/me/instances is TBD on C1; until it lands,
// the page renders the error state with a hint so the operator
// knows what's missing.

function stateBadge(s: MyAgent["state"]) {
  if (s === "running") return <Badge variant="success">running</Badge>;
  if (s === "error") return <Badge variant="destructive">error</Badge>;
  if (s === "stopped") return <Badge variant="secondary">stopped</Badge>;
  return <Badge variant="warning">pending</Badge>;
}

// SEC-21: ttydUrl comes from the backend and goes straight into an <a href>.
// React escapes text but NOT the href scheme, so a poisoned `javascript:` URI
// would execute on click. Only hand back http(s)/ws(s) URLs; anything else
// renders no button instead of a click-to-execute sink.
function safeTtydUrl(url: string | undefined): string | undefined {
  return url && /^(https?|wss?):\/\//i.test(url) ? url : undefined;
}

// W-5: detect platform for the mount hint. We render the UNC path as the
// authoritative copy target (it's what every OS accepts in some form), and
// show a platform-specific *mount hint* below it.
function isMacLike(): boolean {
  if (typeof navigator === "undefined") return false;
  const p = navigator.platform ?? "";
  // Modern Safari/Chrome on macOS still expose "MacIntel"; iPadOS reports
  // the same. Both want smb://, not the UNC backslash form.
  return /Mac|iPhone|iPad|iPod/.test(p);
}

// Convert UNC `\\host\share\path` → smb URL for mac/iOS Finder.
function uncToSmbUrl(unc: string): string {
  const trimmed = unc.replace(/^\\\\/, "");
  return "smb://" + trimmed.split("\\").join("/");
}

function FilesharePath({ path }: { path: string }) {
  const mac = isMacLike();
  const display = path;
  const copyTarget = mac ? uncToSmbUrl(path) : path;
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-2 flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">workspace:</span>
      <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{display}</code>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-6 px-2 text-xs"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(copyTarget);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            // clipboard blocked — leave UI silent rather than throwing
          }
        }}
      >
        {copied ? "copied" : mac ? "copy smb://" : "copy"}
      </Button>
    </div>
  );
}

function QuotaCard({ usage }: { usage: MyUsage }) {
  const total = usage.quota_total ?? 0;
  const used = usage.quota_used ?? usage.total_tokens;
  const pct = total > 0 ? Math.round((used / total) * 100) : 0;
  return (
    <Card>
      <CardContent className="space-y-2 p-4">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Token quota</span>
          <span className="tabular-nums text-muted-foreground">
            {used.toLocaleString()} / {total.toLocaleString()} ({pct}%)
          </span>
        </div>
        <Progress value={used} max={total || 1} />
      </CardContent>
    </Card>
  );
}

export default function MyAgents() {
  const [agents, setAgents] = useState<MyAgent[]>([]);
  const [usage, setUsage] = useState<MyUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Fetch instances + usage in parallel; usage failure is non-fatal so a
    // missing /usage endpoint doesn't blank the agents list.
    Promise.all([api.listMyAgents(), api.myUsage(30).catch(() => null)])
      .then(([data, u]) => {
        setAgents(data.agents);
        setUsage(u);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? e.message : String(e));
        setLoading(false);
      });
  }, []);

  const hasQuota = useMemo(
    () => usage != null && (usage.quota_total ?? 0) > 0,
    [usage],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">My Agents</h1>
          <p className="text-sm text-muted-foreground">
            Running VMs provisioned for you. Click open to launch a ttyd terminal.
          </p>
        </div>
      </div>

      {hasQuota && usage && <QuotaCard usage={usage} />}

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
              C1 endpoint <code>/api/me/instances</code> is not implemented yet — this view will
              populate once the control plane ships it.
            </div>
          </CardContent>
        </Card>
      )}
      {!loading && !error && agents.length === 0 && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            No agents yet. Head over to{" "}
            <a href="/request" className="underline">
              Request Agent
            </a>{" "}
            to ask for one.
          </CardContent>
        </Card>
      )}
      {!loading &&
        !error &&
        agents.map((a) => (
          <Card key={a.id}>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{a.name}</span>
                    {stateBadge(a.state)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {a.template} · created {new Date(a.createdAt).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {a.state === "running" && (
                    <Button asChild size="sm">
                      <Link to={`/terminal/${encodeURIComponent(a.id)}`}>Open Terminal</Link>
                    </Button>
                  )}
                  {safeTtydUrl(a.ttydUrl) && a.state === "running" && (
                    <Button asChild size="sm" variant="outline">
                      <a href={safeTtydUrl(a.ttydUrl)} target="_blank" rel="noreferrer">
                        Open ttyd
                      </a>
                    </Button>
                  )}
                </div>
              </div>
              {a.fileshare_path && <FilesharePath path={a.fileshare_path} />}
            </CardContent>
          </Card>
        ))}
    </div>
  );
}
