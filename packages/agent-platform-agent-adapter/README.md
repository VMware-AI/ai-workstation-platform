# agent-platform-agent-adapter (C20)

Multi-agent abstraction layer + the `agent` CLI. A single `AgentBackend` protocol
fronts the agents (default-install: **xiaoguai** + goose; also available: qcoder /
claude-code) so a user can list, switch, run a prompt, or chat — and each run emits
a telemetry event to the control plane (C1) via the C7 shim.

## 功能

- **统一协议** — `AgentBackend` Protocol（`protocol.py`）定义 `launch_spec()` /
  `health_check()`，各 adapter 各自实现并在 import 时自动注册到 `REGISTRY`。
- **`agent` CLI** — 用户入口（Click），管理默认 agent 并分发 prompt。
- **Runner** — `run_agent()` 起子进程、捕获 stdout/stderr、控超时，**不抛异常**，
  并把一条 `TelemetryEvent` 发给 shim（成功失败都发）。
- **Telemetry bridge** — 可选 `[ingest]` extra 把事件桥接到 C7
  (`agent-platform-telemetry-shim`) → C1 的 `/api/ingest`。

## 安装与使用

```bash
uv sync                       # 或 pip install -e ".[ingest]"（带 C7 上报）

agent list                    # 列出已注册 agent，标星当前默认
agent use xiaoguai            # 设默认（存到 ~/.config/agent-platform/agent_default）
agent health --name xiaoguai      # 检查二进制是否在 PATH，exit 0/1
agent run --with xiaoguai "find bugs in main.py"   # 跑一次 prompt
agent chat                    # 交互式 REPL（每行发给 agent，Ctrl-D 退出）
```

### 配置（env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_PLATFORM_USER` | `$USER` | 运行身份 |
| `AGENT_PLATFORM_WORKSPACE` | `~/workspace` | agent 的工作目录 |
| `AGENT_PLATFORM_LLM_GATEWAY_URL` | `http://localhost:4000` | LiteLLM 网关（C5），各 adapter 内部路由到自己的 API |
| `AGENT_PLATFORM_LLM_API_KEY` | — | 传给 adapter 的 key |

### adapter 差异（xiaoguai/goose 为默认）

| | xiaoguai | goose | qcoder | claude-code |
|---|---|---|---|---|
| 二进制 | `xiaoguai` | `goose` | `qwen` | `claude` |
| 协议 | OpenAI | OpenAI | OpenAI | Anthropic |
| 网关路由 | `…/v1` | 原始 URL | `…/v1` | `…/anthropic` |
| 一次性调用 | `chat --prompt` | `run -t` | `--model -p` | `-p --model` |

### 测试

```bash
uv run pytest      # CLI / 协议 / runner / telemetry，使用假二进制，无需真 agent
```

## 结构

```
src/agent_platform_agent_adapter/
├── protocol.py        # AgentBackend / AgentLaunchSpec / UserContext 契约
├── registry.py        # 全局 REGISTRY（name → backend）
├── runner.py          # run_agent()：子进程 + 超时 + 发遥测
├── cli.py             # agent CLI：list / use / health / run / chat
├── telemetry.py       # TelemetryEvent + NullShim / CircuitBreakerShim
├── telemetry_ingest.py# 可选：C20 → C7 → C1 bridge（[ingest] extra）
└── adapters/          # xiaoguai.py / goose.py / qcoder.py / claude_code.py（import 即注册）
```

**加一个新 adapter**：实现 `AgentBackend` 协议，在 `adapters/__init__.py` 注册即可。

设计依据见 [v2 设计](../../docs/plans/2026-05-17-agent-platform-design.md)。
