# agent-platform-console (C2)

Admin web console — Vite + React 18 + TypeScript + Tailwind + shadcn/ui primitives.
Talks to the C1 control plane (PR #57).

## Local dev

```bash
cd packages/agent-platform-console
pnpm install
pnpm dev              # http://localhost:5173 (proxies /admin/* → :8000)
pnpm typecheck
pnpm test
pnpm build
```

Override the API base URL with `VITE_CONTROL_BASE_URL=https://agent-platform.example.com pnpm build`.

## Layout

```
src/
├── App.tsx              # routes
├── main.tsx             # bootstrap
├── components/
│   ├── Layout.tsx       # sidebar + outlet
│   └── ui/              # shadcn primitives (button, card, table, badge)
├── lib/
│   ├── api.ts           # typed fetch client mirroring C1's /admin/* surface
│   └── cn.ts            # tailwind class merger
└── pages/              # 见下，按区域分组的视图
```

`pages/` 当前视图（route-group layout 为 `*Layout.tsx`）：

| 区域 | 页面 |
|---|---|
| 总览 | `Overview.tsx` |
| 运维 (Operations) | `Deployments.tsx` · `DeploymentDetail.tsx` · `VMs.tsx` · `Upgrades.tsx` · `Approvals.tsx` |
| vCenter | `VCenterConnections.tsx` · `VCenterInventory.tsx` · `VCenterTemplates.tsx` |
| 平台 | `ComponentsHealth.tsx` · `TokenUsage.tsx` · `Audit.tsx` |
| Layout | `OperationsLayout.tsx` · `VCenterLayout.tsx` · `ReleasesLayout.tsx` · `LifecycleLayout.tsx` |

所有视图通过 `lib/api.ts` 访问 C1 控制面的 `/admin/*` 接口；未接通真实数据的视图会显示 “backend stub” 标记。

## Scope

已从 1.10 的 3 视图骨架扩展为多区域控制台（总览 / 运维 / vCenter / 平台）。
后续细化项见 [`docs/plans/2026-05-17-subtask-breakdown.md`](../../docs/plans/2026-05-17-subtask-breakdown.md)。
