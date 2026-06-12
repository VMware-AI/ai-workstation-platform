// Typed fetch client for the C12 user portal.
// Talks to the C1 control plane (same surface as the C2 console at
// /admin/approvals/...), plus future per-user endpoints (/api/me/...)
// that don't exist on the backend yet — those pages will render the
// error state until the C1 endpoints land.

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

// === My Agents (TBD endpoint — page renders error state until C1 wires it) ===
export type MyAgent = {
  id: string;
  name: string;
  template: string;
  state: "pending" | "running" | "stopped" | "error";
  ttydUrl?: string;
  createdAt: string;
  // W-5: UNC path for the per-user workspace dataset on the fileshare.
  // Null when C1 has no fileshare_base configured (dev / pre-C19); the
  // card shows "—" rather than a broken UNC.
  fileshare_path?: string | null;
};
export type MyAgentList = { agents: MyAgent[]; _stub?: boolean };

// === Approval (reuses C13 surface mounted by C1 at /admin/approvals) ===
export type ApprovalState = "pending" | "approved" | "rejected";
export type MyApproval = {
  id: number;
  requester: string;
  package: string;
  justification: string;
  state: ApprovalState;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string | null;
};

// === My Usage (TBD endpoint) ===
export type UsageDay = { date: string; tokens: number };
export type MyUsage = {
  days: UsageDay[];
  total_tokens: number;
  // W-5: soft quota; C5 wires real per-user limits in M2.2. The portal
  // progress bar reads `quota_used / quota_total` rather than diverging
  // from total_tokens.
  quota_total?: number;
  quota_used?: number;
  _stub?: boolean;
};

// === ttyd WebSocket URL (TBD endpoint on C1) ===
// C1 should return a short-lived signed WS URL pointing at the ttyd
// daemon running inside the user's VM. Until C1 ships it, the Terminal
// page falls back to a local-echo demo mode so the UI still demos end
// to end (M1 demo step 5).
export type TtydUrl = { url: string };

export const api = {
  healthz: () => request<Health>("/healthz"),
  listMyAgents: () => request<MyAgentList>("/api/me/instances"),
  listMyApprovals: (requester: string) =>
    request<MyApproval[]>(
      `/admin/approvals/requests?requester=${encodeURIComponent(requester)}&limit=50`,
    ),
  submitApproval: (body: { requester: string; package: string; justification: string }) =>
    request<MyApproval>("/admin/approvals/requests", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  myUsage: (days = 30) => request<MyUsage>(`/api/me/usage?days=${days}`),
  getTtydUrl: (vmId: string) =>
    request<TtydUrl>(`/api/me/instances/${encodeURIComponent(vmId)}/ttyd-url`),
};
