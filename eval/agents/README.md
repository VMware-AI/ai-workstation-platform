# Agent Adapter — 安装与配置文档

3 个候选 agent 的安装、配置、健康检查与对接 LiteLLM 网关的指南。

| Agent | 上游 | 协议 | License | 默认推荐 |
|---|---|---|---|:---:|
| [qcoder](qcoder/install.md) | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | OpenAI 兼容 | Apache 2.0 | ✅ M1 必选 |
| [Goose](goose/install.md) | [block/goose](https://github.com/block/goose) | OpenAI / Anthropic / Ollama | Apache 2.0 | ✅ M1 必选 |
| [Claude Code](claude-code/install.md) | [@anthropic-ai/claude-code](https://www.npmjs.com/package/@anthropic-ai/claude-code) | Anthropic | Proprietary | ⚠️ 待 license 确认 (#34) |

## 共同前置

- Node.js ≥ 20
- 网络：能访问 LiteLLM 网关（默认 `http://localhost:4000`，env `AGENT_PLATFORM_LLM_GATEWAY_URL`）
- API key：`AGENT_PLATFORM_LLM_API_KEY`（默认 `sk-eval-master-key`，仅评测用）

## Hello-world 验证

```bash
qwen --model qwen-coder-32b -p "用 Python 写一行: print('hello from qcoder')"
goose run -t "用 Python 写一行: print('hello from goose')" --quiet
claude -p "用 Python 写一行: print('hello from claude-code')"
```

返回包含 `hello from` 字样即通。验收截图与版本信息按
[`verification-template.md`](verification-template.md) 记录。

## 注意（v1 doc 限制）

本 PR 仅提供**文档 + 自动化脚本**；
**实际 install 命令未在 CI 上跑过**（npm/curl 都需要网络，部分 agent 需要 GPU 后端才能 hello-world）。
verifier 接 #6 任务时需在本机实跑一次，把"已验证版本"回填到对应 install.md 顶部，
并把截图/日志链接写入 verification-template。
