// Token usage API client for the C2 admin console.
//
// Talks to two C1 control-plane endpoints (not yet implemented — see
// docs/plans/2026-05-17-feature-and-task-backlog.md task F-A08 / C1 task 1.10.2):
//   GET /admin/usage/summary?window=24h|7d|30d
//   GET /admin/usage/invocations?limit=N
//
// TODO(C1): replace mock fallback once the control plane lands these routes.
// Data is sourced from C5 agent-platform-llm-gateway (LiteLLM accounting, PR #100)
// + C7 agent-platform-telemetry-shim (PR #99, #102) which tags each invocation
// with the calling agent + user before forwarding to C1 ingest (PR #110).
//
// Behaviour: fetch the real endpoint first; on any network/HTTP error fall
// back to deterministic mock fixtures and flag the response with `_mock: true`
// so the UI can render a "demo data" badge — same pattern as `_stub` on the
// existing audit/vm endpoints.

import { ApiError } from "@/lib/api";

export type UsageWindow = "24h" | "7d" | "30d";

export type UsageBucket = {
  key: string;
  tokens: number;
  cost_usd: number;
};

export type UsageSummary = {
  window: UsageWindow;
  total_tokens: number;
  total_cost_usd: number;
  active_users: number;
  active_agents: number;
  by_agent: UsageBucket[];
  by_user: UsageBucket[];
  by_model: UsageBucket[];
  _mock?: boolean;
};

export type InvocationRow = {
  id: string;
  at: string;
  user: string;
  agent: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
};

export type InvocationListResponse = {
  invocations: InvocationRow[];
  limit: number;
  _mock?: boolean;
};

const BASE_URL = (import.meta.env.VITE_CONTROL_BASE_URL ?? "").replace(/\/$/, "");

async function tryFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    credentials: "include",
    headers: { "content-type": "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${path} → ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const tokenUsageApi = {
  async summary(window: UsageWindow): Promise<UsageSummary> {
    try {
      return await tryFetch<UsageSummary>(`/admin/usage/summary?window=${window}`);
    } catch {
      return { ...mockSummary(window), _mock: true };
    }
  },
  async invocations(limit = 20): Promise<InvocationListResponse> {
    try {
      return await tryFetch<InvocationListResponse>(`/admin/usage/invocations?limit=${limit}`);
    } catch {
      return { ...mockInvocations(limit), _mock: true };
    }
  },
};

// ----- mock fixtures (deterministic; M1 demo seed) ---------------------------

const MOCK_AGENTS = ["claude-code", "goose", "cursor", "windsurf"] as const;
const MOCK_USERS = ["alice", "bob", "carol", "dave", "erin"] as const;
const MOCK_MODELS = [
  "claude-sonnet-4.6",
  "claude-haiku-4.5",
  "gpt-4o-mini",
  "deepseek-v3",
] as const;

function bucket(keys: readonly string[], base: number, step: number): UsageBucket[] {
  return keys.map((key, i) => {
    const tokens = base + i * step;
    return { key, tokens, cost_usd: Number((tokens * 0.000002).toFixed(4)) };
  });
}

function sum(buckets: UsageBucket[], field: "tokens" | "cost_usd"): number {
  return buckets.reduce((acc, b) => acc + b[field], 0);
}

export function mockSummary(window: UsageWindow): UsageSummary {
  const scale = window === "24h" ? 1 : window === "7d" ? 6 : 24;
  const by_agent = bucket(MOCK_AGENTS, 80_000 * scale, 12_000 * scale);
  const by_user = bucket(MOCK_USERS, 40_000 * scale, 9_000 * scale);
  const by_model = bucket(MOCK_MODELS, 60_000 * scale, 15_000 * scale);
  return {
    window,
    total_tokens: sum(by_agent, "tokens"),
    total_cost_usd: Number(sum(by_agent, "cost_usd").toFixed(2)),
    active_users: MOCK_USERS.length,
    active_agents: MOCK_AGENTS.length,
    by_agent,
    by_user,
    by_model,
  };
}

export function mockInvocations(limit: number): InvocationListResponse {
  const now = Date.parse("2026-05-23T12:00:00Z");
  const invocations: InvocationRow[] = Array.from({ length: Math.min(limit, 20) }, (_, i) => {
    const prompt = 1200 + i * 137;
    const completion = 400 + i * 53;
    return {
      id: `inv-${String(i + 1).padStart(4, "0")}`,
      at: new Date(now - i * 5 * 60_000).toISOString(),
      user: MOCK_USERS[i % MOCK_USERS.length],
      agent: MOCK_AGENTS[i % MOCK_AGENTS.length],
      model: MOCK_MODELS[i % MOCK_MODELS.length],
      prompt_tokens: prompt,
      completion_tokens: completion,
      cost_usd: Number(((prompt + completion) * 0.000002).toFixed(4)),
    };
  });
  return { invocations, limit };
}
