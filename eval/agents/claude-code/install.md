# Claude Code — 安装与配置

> 上游：https://www.npmjs.com/package/@anthropic-ai/claude-code
> Adapter 类型：Anthropic protocol
> ⚠️ **License 待确认**（见 [#34](https://github.com/VMware-AI/ai-workstation-platform/issues/34)）；本文档假设客户已与 Anthropic 谈妥企业分发权
> 已验证版本：(待 #6 接手人回填)

## 前置

- Node.js ≥ 20
- LiteLLM 网关已启用 Anthropic 协议透传（参考 #5 / `packages/agent-platform-llm-gateway`）

## 安装

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

> air-gap：从 C4 内部 npm registry 装，二进制与 model 资源不需要额外下载。

## 配置：指向 LiteLLM 网关（Anthropic 协议）

```bash
export ANTHROPIC_BASE_URL="http://localhost:4000/anthropic"
export ANTHROPIC_API_KEY="sk-eval-master-key"
# 可选：覆盖默认模型为本地
export ANTHROPIC_MODEL="qwen-coder-32b"
```

> Claude Code 通过 `ANTHROPIC_BASE_URL` 指向兼容 Anthropic Messages API 的 endpoint；LiteLLM 在 `/anthropic` 路径下做协议转换 (Anthropic ↔ OpenAI)。

## Hello-world

```bash
claude -p "用 Python 写一行: print('hello from claude-code')"
```

## 健康检查

```bash
claude --version || { echo "claude not installed"; exit 1; }
curl -sf "$ANTHROPIC_BASE_URL/v1/messages" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"'"$ANTHROPIC_MODEL"'","max_tokens":5,"messages":[{"role":"user","content":"ok"}]}' \
  >/dev/null || { echo "gateway anthropic passthrough not working"; exit 1; }
```

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `Authentication failed` | 网关未识别 API key | 检查 LiteLLM `general_settings.master_key` |
| 404 on `/anthropic` | LiteLLM 未启 `anthropic_passthrough` | 改 LiteLLM config `anthropic_passthrough.enabled: true` 重启 |
| 模型名报错 | LiteLLM model_list 没注册 | 在 LiteLLM config.yaml 加 `claude-3.5-sonnet → 本地模型` 路由 |
| 工具调用很差 | 后端是 quantized 小模型 | 评测时只用 Anthropic 协议 + 大模型组合做对比；不要用 7B/14B 量化跑 |

## License 注意（⚠️ 重要）

商业 air-gap 客户分发 Claude Code 前 **必须** 满足下面之一：
- Anthropic Enterprise / Self-hosted 协议（联系商务）
- 每用户独立 Anthropic 账号（不适合 air-gap）
- 改用 [Aider](https://aider.chat) 或 [OpenHands](https://github.com/All-Hands-AI/OpenHands) 作为开源备选（已在 M0.2 评测对比清单内）

详见 [#34](https://github.com/VMware-AI/ai-workstation-platform/issues/34) 的法务跟踪。

## 在 agent-platform runner 里调用

参考 `eval/runner.py` 中 `AGENTS["claude-code"]` 项。
