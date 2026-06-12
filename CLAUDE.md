# AI Workstation Platform — Claude Code 协作约定

> Monorepo for the AI Workstation Platform (内部代号 Agent Platform).
> 14 个 packages, uv + pnpm workspace, FastAPI + React + Packer + vLLM + NSX-T.

## 核心工作原则

### 1. Think Before Coding — 暴露困惑
- 先明确陈述假设，不确定就问
- 多种解读并存 → 全部列出
- 不清楚的停下，点名困惑，提问

### 2. Simplicity First — 最小解
- 不加未被要求的功能、抽象、灵活性
- 不为不可能发生的场景加错误处理
- 200 行能写成 50 行就重写

### 3. Surgical Changes — 精准改动
- 编辑现有代码时不顺手改周围
- 匹配现有风格
- 注意到无关死代码 → 说出来，别删除
- 每一行改动都能追溯到用户原始需求

### 4. Goal-Driven Execution — 可验证成功标准
- "加校验" → "写无效输入的测试，让它通过"
- "修 bug" → "写复现 bug 的测试，让它通过"

---

## 仓库结构

```
ai-workstation-platform/
├── packages/
│   ├── agent-platform-control/       (C1) FastAPI 控制面
│   ├── agent-platform-console/       (C2) React 管理控制台
│   ├── agent-platform-image/         (C3) Packer 镜像
│   ├── agent-platform-repo/          (C4) devpi 私有 PyPI
│   ├── agent-platform-llm-gateway/   (C5) LiteLLM 网关
│   ├── agent-platform-llm-runtime/   (C6) vLLM 部署
│   ├── agent-platform-telemetry-shim/(C7) 多 agent telemetry
│   ├── agent-platform-installer/     (C8) 客户安装器
│   ├── agent-platform-scale-bundle/  (C9) cosign 打包
│   ├── agent-platform-portal/        (C12) React 用户门户 ⚠️ DEPRECATED (退场中)
│   ├── agent-platform-approval/      (C13) 审批引擎
│   ├── agent-platform-pool-scheduler/(C14) 资源池 + NSX 调度
│   ├── agent-platform-secrets/       (C18) Vaultwarden
│   ├── agent-platform-fileshare/     (C19) vSAN File Services SMB
│   ├── agent-platform-agent-adapter/ (C20) Agent Protocol
│   └── agent-platform-web-ui/        (C21) Next.js web UI + 控制面（收敛中）
│   # 设计文档（HLD/LLD、architecture、plans、research、runbooks、status、decks）
│   # 已迁出本公开仓库，存放在私有仓库 ai-workstation-platform-design
├── poc/                       M0 PoC 脚本（fileshare/nsx-dfw/...）
├── eval/                      M0.2 agent + LLM 评测
└── .github/                   CI / Issue 模板 / CODEOWNERS
```

---

## 开发规范

### Python (后端)

- **PEP 8** + ruff format + ruff check
- 所有函数签名必须有 type hint
- 优先 `@dataclass(frozen=True)` / `NamedTuple` 实现不可变
- pytest 80% coverage 起步
- Bandit 0 个 Medium+ issue
- 不用 print，用 logging
- 错误处理三层：轻量重试 → 教学性错误 → circuit breaker（参考 vmware-skill）

### TypeScript (前端)

- React 18 + Vite + TypeScript strict
- pnpm 唯一包管理器
- ESLint + Prettier
- shadcn/ui 为默认组件库
- echarts 做数据可视化

### Git

- 分支：`main`（保护）+ `feat/<owner>-<task-id>`
- Commit message：`<type>: <scope> <description>`，type ∈ feat/fix/refactor/docs/test/chore/perf/ci
- PR 必须 ≥ 1 人 review 才能 merge
- PR title 含 `[CXX]` 前缀标组件归属

### CI 必跑

- ruff format --check + ruff check
- bandit -r packages/*/
- pytest --cov（80% gate）
- pnpm typecheck + pnpm lint
- cosign 验签（如改 scale-bundle）

---

## 安全（mandatory）

- 绝不在仓库出现明文密码 / API key / IP / 客户名
- 凭据走 `.env`（chmod 600）+ Vaultwarden（C18）
- 所有 user input 必须 schema 校验
- 所有来自 vSphere / NSX / 第三方 API 的文本经 `_sanitize()`
- 破坏性操作必须双重确认 + `--dry-run`
- 写操作必须走 `@vmware_tool` 装饰器记审计

---

## 任务领取

- 任务源 / 子任务清单：私有设计仓库 `ai-workstation-platform-design` 的 `docs/plans/`
- Issue 标签：component:CXX / milestone:MX / type:XX / size:S|M|L / priority:PX
- 一人同时最多 in-progress 2 个 Issue
- 超 size 估时 50% → 评论求助，不闷头

---

## 给 Claude Code Agent 的提示

- 优先用 vmware-skill 家族提供的工具（vmware-aiops / vmware-nsx / vmware-policy）做基础操作，不重写
- 任何对 vCenter / NSX 的破坏性操作走 `@vmware_tool` 装饰器
- 写 PR description 时引用本仓库 Issue 编号 + 对应 backlog task ID
- 遇阻塞超 30 分钟 → 写 issue 评论求救，不要绕

---

## 引用文档

- v2 设计 / Backlog / 子任务清单 / HLD·LLD / architecture：私有仓库 `ai-workstation-platform-design`
