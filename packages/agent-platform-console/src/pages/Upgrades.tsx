import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import {
  upgradesApi,
  type PlanRequest,
  type PlanResponse,
  type Upgrade,
  type UpgradeState,
} from "@/lib/api/upgrades";

type StateVariant = "secondary" | "default" | "warning" | "success" | "destructive";

const STATE_VARIANT: Record<UpgradeState, StateVariant> = {
  planned: "secondary",
  provisioning_blue: "default",
  home_volume_attaching: "default",
  blue_ready: "default",
  cutover_in_progress: "warning",
  cutover_done: "warning",
  cleanup_pending: "warning",
  completed: "success",
  failed: "destructive",
  rolled_back: "destructive",
};

type PageState = {
  upgrades: Upgrade[];
  loading: boolean;
  error?: string;
  mock: boolean;
};

type DestructiveKind = "cutover" | "rollback" | "cleanup";

type DestructiveDialog = {
  kind: DestructiveKind;
  upgrade: Upgrade;
  cleanupPreview?: Upgrade; // result of dry_run cleanup
};

export default function Upgrades() {
  const [state, setState] = useState<PageState>({ upgrades: [], loading: true, mock: false });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const [destructive, setDestructive] = useState<DestructiveDialog | null>(null);

  const refresh = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: undefined }));
    try {
      const res = await upgradesApi.list();
      setState({ upgrades: res.upgrades, loading: false, mock: Boolean(res._mock) });
    } catch (e) {
      setState({ upgrades: [], loading: false, mock: false, error: String(e) });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Upgrades</h1>
          <p className="text-sm text-muted-foreground">
            Blue-green workspace image upgrades. Each row promotes a tenant from one
            image version to the next without disturbing in-flight sessions.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {state.mock && <Badge variant="warning">demo data</Badge>}
          <Button onClick={() => setPlanOpen(true)}>Plan upgrade</Button>
        </div>
      </header>

      {state.error && (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">{state.error}</CardContent>
        </Card>
      )}

      <Card>
        {state.loading && <div className="p-6 text-sm text-muted-foreground">Loading…</div>}
        {!state.loading && state.upgrades.length === 0 && !state.error && (
          <div className="p-6 text-sm text-muted-foreground">
            No upgrades yet. Click <strong>Plan upgrade</strong> to start one.
          </div>
        )}
        {state.upgrades.length > 0 && (
          <Table>
            <THead>
              <TR>
                <TH>ID</TH>
                <TH>Tenant</TH>
                <TH>From → To</TH>
                <TH>State</TH>
                <TH>Created</TH>
                <TH className="text-right">Actions</TH>
              </TR>
            </THead>
            <TBody>
              {state.upgrades.map((u) => (
                <UpgradeRow
                  key={u.id}
                  upgrade={u}
                  expanded={expandedId === u.id}
                  onToggle={() => setExpandedId(expandedId === u.id ? null : u.id)}
                  onAction={(kind) => setDestructive({ kind, upgrade: u })}
                />
              ))}
            </TBody>
          </Table>
        )}
      </Card>

      {planOpen && (
        <PlanDialog
          onClose={() => setPlanOpen(false)}
          onStarted={() => {
            setPlanOpen(false);
            void refresh();
          }}
        />
      )}

      {destructive && (
        <DestructiveDialogView
          dialog={destructive}
          onClose={() => setDestructive(null)}
          onDone={() => {
            setDestructive(null);
            void refresh();
          }}
          onCleanupPreview={(preview) =>
            setDestructive((prev) => (prev ? { ...prev, cleanupPreview: preview } : prev))
          }
        />
      )}
    </div>
  );
}

// ----- row + expansion -------------------------------------------------------

function UpgradeRow({
  upgrade,
  expanded,
  onToggle,
  onAction,
}: {
  upgrade: Upgrade;
  expanded: boolean;
  onToggle: () => void;
  onAction: (kind: DestructiveKind) => void;
}) {
  return (
    <>
      <TR>
        <TD className="font-mono text-xs">
          <button className="hover:underline" onClick={onToggle} aria-expanded={expanded}>
            {expanded ? "▾" : "▸"} {upgrade.id}
          </button>
        </TD>
        <TD>{upgrade.tenant_id}</TD>
        <TD className="font-mono text-xs">
          {upgrade.from_version} → {upgrade.to_version}
        </TD>
        <TD>
          <Badge variant={STATE_VARIANT[upgrade.state]}>{upgrade.state}</Badge>
        </TD>
        <TD className="font-mono text-xs">{formatTs(upgrade.created_at)}</TD>
        <TD className="text-right">
          <RowActions upgrade={upgrade} onAction={onAction} />
        </TD>
      </TR>
      {expanded && (
        <TR>
          <TD colSpan={6} className="bg-muted/30">
            <UpgradeDetail upgrade={upgrade} />
          </TD>
        </TR>
      )}
    </>
  );
}

function RowActions({
  upgrade,
  onAction,
}: {
  upgrade: Upgrade;
  onAction: (kind: DestructiveKind) => void;
}) {
  const buttons: { label: string; kind: DestructiveKind; variant: "default" | "destructive" }[] =
    [];
  if (upgrade.can_cutover) {
    buttons.push({ label: "Cutover", kind: "cutover", variant: "default" });
  }
  if (upgrade.can_rollback && !upgrade.is_terminal) {
    buttons.push({ label: "Rollback", kind: "rollback", variant: "destructive" });
  }
  if (upgrade.can_cleanup) {
    buttons.push({ label: "Cleanup", kind: "cleanup", variant: "destructive" });
  }
  if (buttons.length === 0) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <div className="inline-flex gap-2">
      {buttons.map((b) => (
        <Button key={b.kind} size="sm" variant={b.variant} onClick={() => onAction(b.kind)}>
          {b.label}
        </Button>
      ))}
    </div>
  );
}

function UpgradeDetail({ upgrade }: { upgrade: Upgrade }) {
  const blue = upgrade.vms.filter((v) => v.role === "blue");
  const green = upgrade.vms.filter((v) => v.role === "green");
  return (
    <div className="space-y-3 p-4 text-sm">
      <div className="grid grid-cols-2 gap-6">
        <VmList title="Blue (new) VMs" vms={blue} />
        <VmList title="Green (current) VMs" vms={green} />
      </div>
      {upgrade.failure_reason && (
        <div className="rounded border border-destructive/40 bg-destructive/10 p-3 text-xs">
          <span className="font-semibold">Failure: </span>
          <span className="font-mono">{upgrade.failure_reason}</span>
        </div>
      )}
      <div className="text-xs text-muted-foreground">
        Updated {formatTs(upgrade.updated_at)} · can_cutover={String(upgrade.can_cutover)} ·
        can_rollback={String(upgrade.can_rollback)} · can_cleanup={String(upgrade.can_cleanup)}
      </div>
    </div>
  );
}

function VmList({ title, vms }: { title: string; vms: Upgrade["vms"] }) {
  return (
    <div>
      <div className="mb-2 font-medium">{title}</div>
      {vms.length === 0 ? (
        <div className="text-xs text-muted-foreground">none</div>
      ) : (
        <ul className="space-y-1">
          {vms.map((v, i) => (
            <li key={`${v.owner_id}-${i}`} className="font-mono text-xs">
              <span className="font-semibold">{v.intended_name}</span>
              {" · "}
              <span>owner={v.owner_id}</span>
              {" · "}
              <span>status={v.status}</span>
              {v.vm_id && (
                <>
                  {" · "}
                  <span className="text-muted-foreground">{v.vm_id}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ----- plan dialog -----------------------------------------------------------

function PlanDialog({ onClose, onStarted }: { onClose: () => void; onStarted: () => void }) {
  const [tenantId, setTenantId] = useState("");
  const [fromVersion, setFromVersion] = useState("v0.1.0");
  const [toVersion, setToVersion] = useState("v0.2.0");
  const [ownersText, setOwnersText] = useState("alice, bob");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const owners = useMemo(
    () =>
      ownersText
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    [ownersText],
  );

  const canPlan = tenantId && fromVersion && toVersion && owners.length > 0;

  const buildReq = (): PlanRequest => ({
    tenant_id: tenantId,
    from_version: fromVersion,
    to_version: toVersion,
    vms: owners.map((owner) => ({
      owner_id: owner,
      green_vm_id: `wkst-${owner}-${fromVersion}`,
      blue_intended_name: `wkst-${owner}-${toVersion}`,
    })),
  });

  const handlePlan = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await upgradesApi.plan(buildReq());
      setPlan(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleStart = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await upgradesApi.start(buildReq());
      onStarted();
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose}>
      <Card className="w-[520px] max-w-full">
        <CardHeader>
          <CardTitle>Plan upgrade</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Field label="Tenant ID">
            <input
              className="w-full rounded border px-2 py-1 text-sm"
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              placeholder="acme"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="From version">
              <input
                className="w-full rounded border px-2 py-1 text-sm"
                value={fromVersion}
                onChange={(e) => setFromVersion(e.target.value)}
              />
            </Field>
            <Field label="To version">
              <input
                className="w-full rounded border px-2 py-1 text-sm"
                value={toVersion}
                onChange={(e) => setToVersion(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Owners (comma-separated)">
            <textarea
              className="h-20 w-full rounded border px-2 py-1 font-mono text-xs"
              value={ownersText}
              onChange={(e) => setOwnersText(e.target.value)}
            />
            <div className="mt-1 text-xs text-muted-foreground">
              Each owner gets one blue VM cloned from their current green VM.
            </div>
          </Field>

          {plan && (
            <div className="rounded border bg-muted/30 p-3 text-xs">
              <div className="mb-1 font-semibold">Plan preview</div>
              <div>VM count: {plan.vm_count}</div>
              <div>Estimated time: {Math.round(plan.estimated_seconds / 60)} min</div>
              <div>Home volume strategy: {plan.home_volume_strategy}</div>
              <div className="mt-1 font-mono">
                blue names: {plan.blue_vm_names.join(", ")}
              </div>
              {plan._mock && (
                <div className="mt-2">
                  <Badge variant="warning">demo data</Badge>
                </div>
              )}
            </div>
          )}

          {error && <div className="text-sm text-destructive">{error}</div>}
        </CardContent>
        <CardContent className="flex justify-end gap-2 pt-0">
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          {!plan ? (
            <Button onClick={handlePlan} disabled={!canPlan || submitting}>
              {submitting ? "Planning…" : "Preview plan"}
            </Button>
          ) : (
            <Button onClick={handleStart} disabled={submitting}>
              {submitting ? "Starting…" : "Start upgrade"}
            </Button>
          )}
        </CardContent>
      </Card>
    </ModalOverlay>
  );
}

// ----- destructive (cutover / rollback / cleanup) dialog ---------------------

function DestructiveDialogView({
  dialog,
  onClose,
  onDone,
  onCleanupPreview,
}: {
  dialog: DestructiveDialog;
  onClose: () => void;
  onDone: () => void;
  onCleanupPreview: (preview: Upgrade) => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const { kind, upgrade, cleanupPreview } = dialog;
  const needsPreviewFirst = kind === "cleanup" && !cleanupPreview;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      if (kind === "cutover") {
        await upgradesApi.cutover(upgrade.id, true);
      } else if (kind === "rollback") {
        await upgradesApi.rollback(upgrade.id, true);
      } else {
        await upgradesApi.cleanup(upgrade.id, true, false);
      }
      onDone();
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  };

  const runPreview = async () => {
    setPreviewing(true);
    setError(null);
    try {
      const res = await upgradesApi.cleanup(upgrade.id, false, true);
      onCleanupPreview(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setPreviewing(false);
    }
  };

  const title = {
    cutover: `Cut over upgrade ${upgrade.id}?`,
    rollback: `Roll back upgrade ${upgrade.id}?`,
    cleanup: `Clean up upgrade ${upgrade.id}?`,
  }[kind];

  const warning = {
    cutover: "Promotes the blue VMs to receive user traffic. Once cutover_done, rollback is no longer safe.",
    rollback: "Tears down blue VMs and keeps users on green. Only safe before cutover_done.",
    cleanup: "Permanently retires the green VMs. Run dry-run first to see exactly what gets deleted.",
  }[kind];

  return (
    <ModalOverlay onClose={onClose}>
      <Card className="w-[480px] max-w-full">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>{warning}</p>

          {kind === "cleanup" && cleanupPreview && (
            <div className="rounded border bg-muted/30 p-3 text-xs">
              <div className="mb-1 font-semibold">Dry-run preview</div>
              <div>
                Green VMs that will be retired:
              </div>
              <ul className="ml-4 list-disc">
                {cleanupPreview.vms
                  .filter((v) => v.role === "green")
                  .map((v) => (
                    <li key={v.owner_id} className="font-mono">
                      {v.intended_name} (owner: {v.owner_id})
                    </li>
                  ))}
              </ul>
            </div>
          )}

          {!needsPreviewFirst && (
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                aria-label="I understand the risk"
              />
              <span>I understand this is destructive and want to proceed.</span>
            </label>
          )}

          {error && <div className="text-sm text-destructive">{error}</div>}
        </CardContent>
        <CardContent className="flex justify-end gap-2 pt-0">
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          {needsPreviewFirst ? (
            <Button onClick={runPreview} disabled={previewing}>
              {previewing ? "Previewing…" : "Run dry-run"}
            </Button>
          ) : (
            <Button
              variant="destructive"
              onClick={submit}
              disabled={!confirmed || submitting}
            >
              {submitting ? "Submitting…" : "Confirm"}
            </Button>
          )}
        </CardContent>
      </Card>
    </ModalOverlay>
  );
}

// ----- shared bits -----------------------------------------------------------

function ModalOverlay({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()}>{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1 text-xs font-medium text-muted-foreground">{label}</div>
      {children}
    </label>
  );
}

function formatTs(iso: string): string {
  // Keep it deterministic + short — full timestamp shown on hover via tooltip not needed for M1.
  return iso.replace("T", " ").replace(/\.\d+Z$/, "Z");
}
