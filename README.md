# AI Workstation Platform

> AI 工作站平台：一人一 VM、内网共享文件夹直读直写、预装可选 agent（首选 **xiaoguai**，其次 **Goose**；亦支持 Claude Code / qcoder）、全本地 LLM、VCF + NSX 安全隔离。

**License:** [Apache-2.0](LICENSE)

---

## 组件 (Packages)

16 个组件 + 1 个 e2e 测试框架（共 17 个 package），monorepo + uv workspace + pnpm workspace。
**每行的"文档"列直达该组件自己的 README——那是各组件的安装/运行入口。**

| 代号 | 一句话 | 文档（安装/运行入口） |
|---|---|---|
| C1 | 控制面 API + 编排 | [README](packages/agent-platform-control/README.md) |
| C2 | Web 管理控制台 | [README](packages/agent-platform-console/README.md) |
| C3 | 黄金镜像 (Packer) | [README](packages/agent-platform-image/README.md) |
| C4 | 私有 PyPI / 二进制仓库 | [README](packages/agent-platform-repo/README.md) |
| C5 | LiteLLM 网关 + token 记账 | [README](packages/agent-platform-llm-gateway/README.md) |
| C6 | vLLM 推理服务 | [README](packages/agent-platform-llm-runtime/README.md) |
| C7 | 多 agent telemetry adapter | [README](packages/agent-platform-telemetry-shim/README.md) |
| C8 | 客户现场部署安装器（生产 / 离线，**≠ 开发上手**，见下方 §上手 C） | [README](packages/agent-platform-installer/README.md) |
| C9 | 打包 + 签名 + 校验 | [README](packages/agent-platform-scale-bundle/README.md) |
| C12 | 用户自助门户 ⚠️ **DEPRECATED**（退场中） | [README](packages/agent-platform-portal/README.md) |
| C13 | 审批工作流 | [README](packages/agent-platform-approval/README.md) |
| C14 | 资源池 + NSX SG/DFW | [README](packages/agent-platform-pool-scheduler/README.md) |
| C18 | Vaultwarden 集成 | [README](packages/agent-platform-secrets/README.md) |
| C19 | vSAN Fileshare（SMB on vSAN File Services） | [README](packages/agent-platform-fileshare/README.md) |
| C20 | Agent Protocol + agent adapters（默认 xiaoguai/goose）+ `agent` CLI | [README](packages/agent-platform-agent-adapter/README.md) |
| **C21** | **VM 自助开通门户**（Next.js + Prisma + vSphere / Docker）| [README](packages/agent-platform-web-ui/README.md) · **[SETUP（安装+部署+升级）](packages/agent-platform-web-ui/SETUP.md)** |
| C-E2E | 端到端验收测试框架（M1.26 10 步 demo） | [README](packages/agent-platform-e2e/README.md) |

> 路径即代号对应目录：`packages/agent-platform-<...>/`。C21 是目前最常上手的组件（VM 自助门户），它额外有 **SETUP.md** 讲完整安装、真机部署与升级。

## 上手

**这里有三种"安装"，别混淆——按你的目的选一条：**

### A. 开发这个仓库（装工具链）

只装好 monorepo 工具链（写代码、跑测试用），**不会起任何服务**：

```bash
uv sync                  # Python 依赖（uv workspace）
pnpm install             # 前端依赖（pnpm workspace）
uvx pre-commit install   # 提交钩子
```

### B. 跑起 C21 VM 自助门户（最常用 —— 起服务、登录、对真实 vCenter 部署 VM）

C21 用 **npm**（不是 pnpm）。半一键：

```bash
cd packages/agent-platform-web-ui
npm install
npm run setup:env        # 交互生成 .env 密钥（ENCRYPTION_KEY 等）
./start.sh               # 起 Postgres + Redis + worker + dev → http://localhost:3000
```

完整安装 / 真机部署 / 升级 / 排障见 **[C21 SETUP.md](packages/agent-platform-web-ui/SETUP.md)**。
其余组件各自的起法看它们自己的 README（上方组件表"文档"列）。

### C. 客户现场部署（生产 / 离线）

这**不是**开发上手——客户现场的离线一键安装是 **C8 `agent-platform-installer`**，
见 [C8 README](packages/agent-platform-installer/README.md)。

## 协作

- Issue 模板见 `.github/ISSUE_TEMPLATE/`
- 提 PR 前请跑 `pre-commit run -a` + `uv run pytest`

## License

[Apache License 2.0](LICENSE).
