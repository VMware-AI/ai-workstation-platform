// Tiny typed fetch client for the C1 control plane (PR #57).
// Surface tracks packages/agent-platform-control/src/agent_platform_control/api/* exactly.
// When the control plane returns `_stub: true` we render the stub badge so
// reviewers know the UI is correctly wired but the backend is still a stub.

const BASE_URL = (import.meta.env.VITE_CONTROL_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    credentials: "include",
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j?.detail ?? detail;
    } catch {
      // body wasn't json, keep statusText
    }
    throw new ApiError(res.status, `${path} → ${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export type Health = { status: string };
export type Version = { version: string; commit?: string };

export type VmRow = {
  id: string;
  name: string;
  tenant: string;
  state: "running" | "stopped" | "creating" | "error";
  ip?: string;
};

export type VmListResponse = {
  vms: VmRow[];
  _stub?: boolean;
};

export type AuditEntry = {
  id: string;
  at: string;
  actor: string;
  action: string;
  target: string;
};

export type AuditListResponse = {
  entries: AuditEntry[];
  limit: number;
  _stub?: boolean;
};

// C13 agent-platform-approval surface. C1 mounts the router at /admin/approvals
// so URLs match the existing /admin/vms + /admin/audit shape.
export type ApprovalState = "pending" | "approved" | "rejected";

export type ApprovalAuditEvent = {
  id: number;
  event_type: "submitted" | "approved" | "rejected" | "comment";
  actor: string;
  at: string;
  payload: Record<string, unknown>;
};

export type ApprovalRequest = {
  id: number;
  requester: string;
  package: string;
  justification: string;
  state: ApprovalState;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string | null;
  audit_events: ApprovalAuditEvent[];
};

export type ApprovalListFilters = {
  state?: ApprovalState;
  requester?: string;
  limit?: number;
};

function approvalListUrl(filters: ApprovalListFilters): string {
  const params = new URLSearchParams();
  if (filters.state) params.set("state", filters.state);
  if (filters.requester) params.set("requester", filters.requester);
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return `/admin/approvals/requests${qs ? `?${qs}` : ""}`;
}

// === Admin Console Redesign (docs/architecture/21) — R-2/R-3 ===

export type VCenterDescriptor = {
  name: string;
  host: string;
  port?: number;
  user?: string;
  verify_ssl?: boolean;
  configured_via: "env" | "config";
  note?: string;
};

export type VCenterListResponse = {
  vcenters: VCenterDescriptor[];
  _single_only: boolean;
};

export type VCenterHealthResponse = {
  name: string;
  host: string;
  status: "ok" | "timeout" | "error";
  api_version?: string;
  full_name?: string;
  instance_uuid?: string;
  error?: string;
};

export type VCenterInventoryResponse = {
  name: string;
  cached: boolean;
  ttl_s: number;
  hosts: Record<string, unknown>[];
  clusters: Record<string, unknown>[];
  datastores: Record<string, unknown>[];
  networks: Record<string, unknown>[];
  counts: { hosts: number; clusters: number; datastores: number; networks: number };
};

export type VCenterTemplatesResponse = {
  name: string;
  templates: Record<string, unknown>[];
  count: number;
};

export type ComponentRow = {
  id: string;
  label: string;
  kind: "http" | "library" | "cli" | "placeholder";
  status: "ok" | "unreachable" | "unknown" | "not_a_service" | "timeout" | "error";
  version?: string;
  note?: string;
  error?: string;
  probed?: string;
};

export type ComponentsHealthResponse = { components: ComponentRow[] };

export type AdminEvent = {
  id: number;
  actor: string;
  operation: string;
  resource: string;
  result: "success" | "failure" | "denied";
  ts: string | null;
  params: Record<string, unknown> | null;
};

// W-1 batch create — maps to existing POST /v1/deployments.
// Body shape matches DeploymentCreate in
// packages/agent-platform-control/.../api/deployments.py.
export type DeploymentItemIn = {
  owner_id: string;
  intended_name: string;
};

export type DeploymentCreate = {
  tenant_id: string;
  template: string;
  image_version: string;
  items: DeploymentItemIn[];
};

export type DeploymentResponse = {
  id: string;
  tenant_id: string;
  template: string;
  image_version: string;
  state: string;
  counts: { requested: number; succeeded: number; failed: number };
  created_at: string;
  updated_at: string;
};

// W-2 topology — maps to GET /admin/vms/topology (added in this PR).
export type TopologyNode = {
  id: string;
  name: string;
  category: "vcenter" | "vm";
  state: string;
  tenant: string | null;
};

export type TopologyEdge = {
  source: string;
  target: string;
};

export type TopologyResponse = {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
};

export type AdminEventsResponse = {
  events: AdminEvent[];
  limit: number;
  since: string | null;
};

export type OverviewResponse = {
  generated_at: string;
  control_plane: { status: string; version: string };
  vcenter: { status: string; note?: string };
  runtime: { status: string; note?: string };
  counts: {
    vms_total: number;
    vms_running: number;
    deployments_total: number;
    deployments_provisioning: number;
    tokens_today: number;
  };
};

export const api = {
  healthz: () => request<Health>("/healthz"),
  readyz: () => request<Health>("/readyz"),
  version: () => request<Version>("/version"),
  listVms: () => request<VmListResponse>("/admin/vms"),
  listAudit: (limit = 100) => request<AuditListResponse>(`/admin/audit?limit=${limit}`),
  listApprovals: (filters: ApprovalListFilters = {}) =>
    request<ApprovalRequest[]>(approvalListUrl(filters)),
  getApproval: (id: number) =>
    request<ApprovalRequest>(`/admin/approvals/requests/${id}`),
  approveRequest: (id: number, body: { admin: string; reason?: string }) =>
    request<ApprovalRequest>(`/admin/approvals/requests/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rejectRequest: (id: number, body: { admin: string; reason: string }) =>
    request<ApprovalRequest>(`/admin/approvals/requests/${id}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Redesign R-2/R-3 surface
  overview: () => request<OverviewResponse>("/admin/overview"),
  listVCenters: () => request<VCenterListResponse>("/admin/vcenters"),
  vcenterHealth: (name: string) =>
    request<VCenterHealthResponse>(`/admin/vcenters/${encodeURIComponent(name)}/health`),
  vcenterInventory: (name: string, refresh = false) =>
    request<VCenterInventoryResponse>(
      `/admin/vcenters/${encodeURIComponent(name)}/inventory${refresh ? "?refresh=true" : ""}`,
    ),
  vcenterTemplates: (name: string) =>
    request<VCenterTemplatesResponse>(
      `/admin/vcenters/${encodeURIComponent(name)}/templates`,
    ),
  componentsHealth: () => request<ComponentsHealthResponse>("/admin/components/health"),
  // W-2: graph payload for Overview circular topology
  getVmsTopology: () => request<TopologyResponse>("/admin/vms/topology"),
  // W-1: submit a batch deployment. Body must match DeploymentCreate.
  createDeployment: (body: DeploymentCreate) =>
    request<DeploymentResponse>("/v1/deployments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  listEvents: (params?: { since?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.since) q.set("since", params.since);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<AdminEventsResponse>(`/admin/events${qs ? `?${qs}` : ""}`);
  },
};
