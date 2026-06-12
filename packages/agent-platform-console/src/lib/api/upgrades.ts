// Blue-green upgrade API client for the C2 admin console (Task 1.12 / F-A06).
//
// Talks to the C1 control-plane endpoints landing in PR #118
// (feat/zw-c1-blue-green-skeleton):
//
//   POST /admin/upgrades/plan
//   POST /admin/upgrades/start
//   GET  /admin/upgrades            (list — added alongside the page)
//   GET  /admin/upgrades/{id}
//   POST /admin/upgrades/{id}/cutover
//   POST /admin/upgrades/{id}/rollback
//   POST /admin/upgrades/{id}/cleanup
//
// TODO(C1): once PR #118 merges, drop the mock fallback. Until then the
// READ paths (list/get) fall back to deterministic fixtures — but only when
// the backend answers 404 (router not mounted yet) — and flag the response
// with `_mock: true` so the UI renders a "demo data" badge — same pattern as
// tokenUsage.ts (PR #115) and the existing `_stub` flag on /admin/vms.
// Network errors and 5xx surface as real errors.
//
// Destructive mutations (cutover/rollback/cleanup) NEVER fall back to mocks:
// a 409/403/500/network failure must be shown to the operator, not rendered
// as a successful state transition.

import { ApiError } from "@/lib/api";

// State machine states must match the strings in
// packages/agent-platform-control/src/agent_platform_control/orchestrator/upgrade_state.py.
export type UpgradeState =
  | "planned"
  | "provisioning_blue"
  | "home_volume_attaching"
  | "blue_ready"
  | "cutover_in_progress"
  | "cutover_done"
  | "cleanup_pending"
  | "completed"
  | "failed"
  | "rolled_back";

export const TERMINAL_STATES: ReadonlySet<UpgradeState> = new Set([
  "completed",
  "failed",
  "rolled_back",
]);

export type UpgradeVmRole = "blue" | "green";

export type UpgradeVm = {
  role: UpgradeVmRole;
  vm_id: string | null;
  intended_name: string;
  owner_id: string;
  status: string;
  error_message: string | null;
};

export type Upgrade = {
  id: string;
  tenant_id: string;
  from_version: string;
  to_version: string;
  state: UpgradeState;
  can_rollback: boolean;
  can_cutover: boolean;
  can_cleanup: boolean;
  is_terminal: boolean;
  failure_reason: string | null;
  vms: UpgradeVm[];
  created_at: string;
  updated_at: string;
  _mock?: boolean;
};

export type UpgradeListResponse = {
  upgrades: Upgrade[];
  _mock?: boolean;
};

export type PlanVmInput = {
  owner_id: string;
  green_vm_id: string;
  blue_intended_name: string;
};

export type PlanRequest = {
  tenant_id: string;
  from_version: string;
  to_version: string;
  vms: PlanVmInput[];
  dry_run?: boolean;
};

export type PlanResponse = {
  tenant_id: string;
  from_version: string;
  to_version: string;
  vm_count: number;
  blue_vm_names: string[];
  estimated_seconds: number;
  home_volume_strategy: string;
  dry_run: boolean;
  _mock?: boolean;
};

const BASE_URL = (import.meta.env.VITE_CONTROL_BASE_URL ?? "").replace(/\/$/, "");

async function tryFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    credentials: "include",
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: string };
      detail = j?.detail ?? detail;
    } catch {
      // body wasn't json
    }
    throw new ApiError(res.status, `${path} → ${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

// Read paths only fall back to fixtures when the route itself is missing
// (backend not mounted yet → 404). Everything else is a real failure.
function isRouteNotMounted(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

export const upgradesApi = {
  async list(): Promise<UpgradeListResponse> {
    try {
      return await tryFetch<UpgradeListResponse>("/admin/upgrades");
    } catch (e) {
      if (!isRouteNotMounted(e)) throw e;
      return { upgrades: mockUpgrades(), _mock: true };
    }
  },
  async get(id: string): Promise<Upgrade> {
    try {
      return await tryFetch<Upgrade>(`/admin/upgrades/${encodeURIComponent(id)}`);
    } catch (e) {
      if (!isRouteNotMounted(e)) throw e;
      const hit = mockUpgrades().find((u) => u.id === id);
      if (!hit) {
        throw new ApiError(404, `upgrade ${id} not found in mock fixtures`);
      }
      return { ...hit, _mock: true };
    }
  },
  async plan(req: PlanRequest): Promise<PlanResponse> {
    try {
      return await tryFetch<PlanResponse>("/admin/upgrades/plan", {
        method: "POST",
        body: JSON.stringify({ dry_run: true, ...req }),
      });
    } catch {
      return { ...mockPlan(req), _mock: true };
    }
  },
  async start(req: PlanRequest): Promise<Upgrade> {
    try {
      return await tryFetch<Upgrade>("/admin/upgrades/start", {
        method: "POST",
        body: JSON.stringify({ ...req, dry_run: false }),
      });
    } catch (e) {
      // Effectful mutation: only fall back when the route isn't mounted.
      // A backend rejection (409/5xx/network) must surface, never render
      // as a fake "started" upgrade.
      if (!isRouteNotMounted(e)) throw e;
      return { ...mockUpgradeFromPlan(req), _mock: true };
    }
  },
  // Destructive mutations: no mock fallback — failures propagate so the
  // confirm dialog can surface them (Upgrades.tsx DestructiveDialogView).
  async cutover(id: string, confirm: boolean): Promise<Upgrade> {
    return tryFetch<Upgrade>(`/admin/upgrades/${encodeURIComponent(id)}/cutover`, {
      method: "POST",
      body: JSON.stringify({ confirm }),
    });
  },
  async rollback(id: string, confirm: boolean): Promise<Upgrade> {
    return tryFetch<Upgrade>(`/admin/upgrades/${encodeURIComponent(id)}/rollback`, {
      method: "POST",
      body: JSON.stringify({ confirm }),
    });
  },
  async cleanup(id: string, confirm: boolean, dryRun: boolean): Promise<Upgrade> {
    return tryFetch<Upgrade>(`/admin/upgrades/${encodeURIComponent(id)}/cleanup`, {
      method: "POST",
      body: JSON.stringify({ confirm, dry_run: dryRun }),
    });
  },
};

// ----- mock fixtures (deterministic; M1 demo seed) ---------------------------

function makeVms(
  owners: readonly string[],
  fromVersion: string,
  blueStatus: string,
  greenStatus: string,
): UpgradeVm[] {
  return owners.flatMap((owner, i) => [
    {
      role: "green" as const,
      vm_id: `vm-green-${owner}-${i}`,
      intended_name: `wkst-${owner}-${fromVersion}`,
      owner_id: owner,
      status: greenStatus,
      error_message: null,
    },
    {
      role: "blue" as const,
      vm_id: blueStatus === "pending" ? null : `vm-blue-${owner}-${i}`,
      intended_name: `wkst-${owner}-blue`,
      owner_id: owner,
      status: blueStatus,
      error_message: null,
    },
  ]);
}

export function mockUpgrades(): Upgrade[] {
  const now = Date.parse("2026-05-23T10:00:00Z");
  const ts = (offsetMin: number) => new Date(now - offsetMin * 60_000).toISOString();
  return [
    {
      id: "upg-0001",
      tenant_id: "acme",
      from_version: "v0.1.0",
      to_version: "v0.2.0",
      state: "blue_ready",
      can_rollback: true,
      can_cutover: true,
      can_cleanup: false,
      is_terminal: false,
      failure_reason: null,
      vms: makeVms(["alice", "bob"], "v0.1.0", "ready", "active"),
      created_at: ts(45),
      updated_at: ts(10),
    },
    {
      id: "upg-0002",
      tenant_id: "globex",
      from_version: "v0.1.0",
      to_version: "v0.2.0",
      state: "cutover_in_progress",
      can_rollback: true,
      can_cutover: false,
      can_cleanup: false,
      is_terminal: false,
      failure_reason: null,
      vms: makeVms(["carol"], "v0.1.0", "ready", "draining"),
      created_at: ts(120),
      updated_at: ts(5),
    },
    {
      id: "upg-0003",
      tenant_id: "initech",
      from_version: "v0.1.0",
      to_version: "v0.2.0",
      state: "cleanup_pending",
      can_rollback: false,
      can_cutover: false,
      can_cleanup: true,
      is_terminal: false,
      failure_reason: null,
      vms: makeVms(["dave", "erin"], "v0.1.0", "ready", "retiring"),
      created_at: ts(720),
      updated_at: ts(60),
    },
    {
      id: "upg-0004",
      tenant_id: "umbrella",
      from_version: "v0.0.9",
      to_version: "v0.1.0",
      state: "completed",
      can_rollback: false,
      can_cutover: false,
      can_cleanup: false,
      is_terminal: true,
      failure_reason: null,
      vms: makeVms(["frank"], "v0.0.9", "ready", "torn_down"),
      created_at: ts(2880),
      updated_at: ts(1440),
    },
    {
      id: "upg-0005",
      tenant_id: "soylent",
      from_version: "v0.1.0",
      to_version: "v0.2.0",
      state: "failed",
      can_rollback: false,
      can_cutover: false,
      can_cleanup: false,
      is_terminal: true,
      failure_reason: "blue VM clone timed out after 600s on host esxi-03",
      vms: makeVms(["gina"], "v0.1.0", "error", "active"),
      created_at: ts(360),
      updated_at: ts(300),
    },
  ];
}

export function mockPlan(req: PlanRequest): PlanResponse {
  const blueNames = req.vms.map((v) => v.blue_intended_name);
  return {
    tenant_id: req.tenant_id,
    from_version: req.from_version,
    to_version: req.to_version,
    vm_count: req.vms.length,
    blue_vm_names: blueNames,
    estimated_seconds: 300 * req.vms.length,
    home_volume_strategy: "copy",
    dry_run: req.dry_run ?? true,
  };
}

export function mockUpgradeFromPlan(req: PlanRequest): Upgrade {
  const now = new Date().toISOString();
  return {
    id: `upg-${Math.random().toString(36).slice(2, 8)}`,
    tenant_id: req.tenant_id,
    from_version: req.from_version,
    to_version: req.to_version,
    state: "planned",
    can_rollback: true,
    can_cutover: false,
    can_cleanup: false,
    is_terminal: false,
    failure_reason: null,
    vms: req.vms.flatMap((v) => [
      {
        role: "green" as const,
        vm_id: v.green_vm_id,
        intended_name: v.green_vm_id,
        owner_id: v.owner_id,
        status: "active",
        error_message: null,
      },
      {
        role: "blue" as const,
        vm_id: null,
        intended_name: v.blue_intended_name,
        owner_id: v.owner_id,
        status: "pending",
        error_message: null,
      },
    ]),
    created_at: now,
    updated_at: now,
  };
}
