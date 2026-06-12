# agent-platform-control (C1)

Control plane for the Agent Platform: a FastAPI service that owns tenants, users,
VM deployments and token accounting, and drives VM provisioning against vCenter
(or vcsim) through an async background worker.

## 功能

- **REST API** — deployments、upgrades、用户自助（`/api/me/*`）、in-VM 心跳、
  cloud-init 秘密交换、C5 网关的 token 用量上报、以及 RBAC-gated 的 `/admin/*`
  库存/拓扑/事件视图。
- **多租户持久化** — SQLAlchemy(async) + Postgres / SQLite，租户作用域过滤。
- **部署编排** — `DeploymentWorker` 异步拉取待处理项，经 `Provisioner`
  （`vmware` 走 pyVmomi，或 `fake` 供开发）克隆/定制/销毁 VM。
- **兄弟组件集成** — 审批走 C13 (`agent-platform-approval`)，秘密解析走
  C18 (`agent-platform-secrets`)，vCenter 连接断路器走 C7
  (`agent-platform-telemetry-shim`)，库存读取走 `vmware-aiops`。

## 本地开发

```bash
uv sync                                 # 装依赖（workspace 内自动解析兄弟组件）
uv run agent-platform-control db init   # 建表（开发默认 SQLite）
uv run agent-platform-control serve     # 起 API，默认 127.0.0.1:8000
# 可选：--host 0.0.0.0 --port 8000 --reload
```

健康检查：`GET /healthz`（存活）、`/readyz`（DB ping）、`/healthz/deep`（worker + cron）。

### 测试

```bash
uv run pytest      # 每个测试用独立的临时 SQLite，无需外部服务
```

## 配置（env 前缀 `AGENT_PLATFORM_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_PLATFORM_DATABASE_URL` | `sqlite+aiosqlite:///./agent-platform-control.db` | 生产设为 `postgresql+asyncpg://…` |
| `AGENT_PLATFORM_ENABLE_WORKER` | `false` | 置 `true` 才启动部署 worker |
| `AGENT_PLATFORM_PROVISIONER_KIND` | — | worker 开启时必填：`fake` 或 `vmware` |
| `AGENT_PLATFORM_ENABLE_FAKE_AUTH` | （见 config） | 生产须关闭，否则用 dev token 会拒绝启动 |

`vmware` provisioner 还需 vCenter 的 URL / 用户 / 密码 / 模板（见 `config.py`）。
完整变量见 `.env.example`。

## 结构

```
src/agent_platform_control/
├── app.py / cli.py / config.py / auth.py / runtime.py   # 应用装配
├── api/          # REST 路由：health, deployments, upgrades, me, heartbeat,
│                 #   cloud_init, ingest, admin/*
├── db/           # SQLAlchemy 模型 + Alembic 迁移（tenant scope 过滤）
└── orchestrator/ # DeploymentWorker + Provisioner(vmware/fake) + 配额/token/清理
```

> 现状：API、ORM（多版本迁移）、后台 worker、RBAC、token 记账、清理 cron 均已实现并有测试覆盖。Keycloak OIDC 接入仍是 M1.2.3 待办（当前为 bearer / X-User 的开发态认证）。

设计依据见 [v2 设计](../../docs/plans/2026-05-17-agent-platform-design.md)。
