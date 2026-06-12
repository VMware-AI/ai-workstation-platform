# agent-platform-portal (C12)

> ⚠️ **DEPRECATED as of 2026-06-01** — see [DEPRECATED.md](./DEPRECATED.md) and
> [doc 31](../../docs/architecture/31-2026-06-01-user-interaction-model-and-portal-removal.md).
> Removed from `make dev-up`; kept only pending deletion. **Do not build new
> features here** — the user-facing surface moved to the agent's own web UI (C21).
> The rest of this README describes the portal as it was before retirement.

End-user self-service portal. Vite + React + Tailwind + tiny shadcn-style ui kit, mirroring the C2 console layout but with user-facing pages instead of admin chrome.

## Pages (Task 1.16)

| Path | Page | Sub | Backend |
|---|---|---|---|
| `/my-agents` | List of VMs provisioned for me, ttyd launch button | 1.16.2 | `GET /api/me/instances` ✅ shipped (`agent_platform_control.api.me:list_my_instances`) |
| `/request` | Form to submit a new agent request | 1.16.3 | `POST /admin/approvals/requests` (C13 via C1, #106) |
| `/usage` | My token consumption (last 30 days) | 1.16.4 | `GET /api/me/usage?days=30` ✅ shipped (`agent_platform_control.api.me:my_usage`) |

My Agents and My Usage now talk to live C1 endpoints (earlier TBD removed 2026-05-30). Request Agent is end-to-end once #104-#106 land.

The Request Agent page works **end to end** once #104 / #105 / #106 land — it posts to the same C13 router the C2 console reads, with the user as `requester`.

## Subtask coverage (Task 1.16)

| Sub | Status | Notes |
|---|---|---|
| 1.16.1 SSO login page | ⬜ | placeholder identity from `sessionStorage["agent-platform:user"]`; Keycloak wiring is a separate PR |
| **1.16.2** My Agents page | ✅ wired to live `/api/me/instances` (2026-05-30) |
| **1.16.3** Request Agent form | ✅ end-to-end via C13 |
| **1.16.4** My Usage page | ✅ wired to live `/api/me/usage` (2026-05-30) |
| 1.16.5 Skill Store | 🚫 deferred to M2 (per backlog Task 1.16.5) |

## Local dev

```bash
cd packages/agent-platform-portal
pnpm install   # workspace root usually does this
pnpm dev       # → http://localhost:5174 (5173 is C2 console)
```

The Vite dev proxy forwards `/admin/*` and `/api/*` to `http://localhost:8000` (C1 control plane). Override via `VITE_CONTROL_BASE_URL` in `.env.local` when deploying behind an ingress.

## Tests

```bash
pnpm test
```

Vitest + jsdom + @testing-library/react. First test asserts the Request Agent form renders with the four package options.

## Identity placeholder

Until SSO lands, the portal reads `window.sessionStorage["agent-platform:user"]` as the current user. Set it in DevTools for local testing:

```js
window.sessionStorage.setItem("agent-platform:user", "alice");
```

It's used as the `requester` field when submitting an approval.
