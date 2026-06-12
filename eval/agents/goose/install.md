# Goose (Block) — 安装与配置

> 上游：https://github.com/block/goose
> Adapter 类型：OpenAI / Anthropic / Ollama provider
> 已验证版本：(待 #6 接手人回填)

## 前置

- Linux / macOS / Windows (WSL)
- 网络能访问 LiteLLM 网关
- 可选：MCP server 二进制（如 vmware-aiops），如用 MCP 工具

## 安装

```bash
# 官方一键脚本（推荐）
curl -fsSL https://github.com/block/goose/releases/latest/download/install.sh | bash

# 或 brew
brew install block/goose/goose

# 验证
goose --version
```

> 客户 air-gap 环境：从 C4 内部仓库下载 release tarball，解压到 `/usr/local/bin/goose`。

## 配置：指向 LiteLLM 网关（OpenAI 协议）

`~/.config/goose/config.yaml`：

```yaml
GOOSE_PROVIDER: openai
OPENAI_HOST: http://localhost:4000
OPENAI_API_KEY: sk-eval-master-key
GOOSE_MODEL: qwen-coder-32b
GOOSE_MODE: auto      # auto | approve | smart-approve
extensions:
  developer:
    enabled: true     # 内置 shell / read / write 工具
```

环境变量等效（runner 用）：

```bash
export GOOSE_PROVIDER=openai
export OPENAI_HOST=http://localhost:4000
export OPENAI_API_KEY=sk-eval-master-key
export GOOSE_MODEL=qwen-coder-32b
```

## Hello-world

```bash
goose run -t "用 Python 写一行: print('hello from goose')" --quiet
```

期望：stdout 含 `hello from goose`。

## MCP server 接入（可选）

```yaml
# ~/.config/goose/config.yaml
extensions:
  vmware-aiops:
    type: stdio
    cmd: vmware-aiops
    args: ["mcp"]
    env:
      VMWARE_AIOPS_CONFIG: ~/.vmware-aiops/config.yaml
```

## 健康检查

```bash
goose --version || { echo "goose not installed"; exit 1; }
curl -sf "${OPENAI_HOST}/v1/models" -H "Authorization: Bearer ${OPENAI_API_KEY}" >/dev/null \
  || { echo "gateway unreachable"; exit 1; }
echo "say ok" | goose run --quiet 2>&1 | tail -3 | grep -i ok
```

## 常见问题

| 现象 | 解决 |
|---|---|
| `Failed to load config` | 删 `~/.config/goose/config.yaml` 重跑 `goose configure` |
| Anthropic 协议 hello world 不通 | Goose 默认 OpenAI；改 Anthropic 需 `GOOSE_PROVIDER=anthropic` + `ANTHROPIC_HOST` |
| stdin 卡住 | 用 `-t` 传 prompt 而非 stdin |
| 工具调用乱选 | 装 [`block/goose` recipes](https://github.com/block/goose/tree/main/recipes) 限定 |

## 在 agent-platform runner 里调用

参考 `eval/runner.py` 中 `AGENTS["goose"]` 项。
