# qcoder (Qwen Code) — 安装与配置

> 上游：https://github.com/QwenLM/qwen-code
> Adapter 类型：OpenAI-compatible CLI
> 已验证版本：(待 #6 接手人回填)

## 前置

- Node.js ≥ 20
- npm 或 pnpm

## 安装

```bash
# 全局装（推荐 eval 场景）
npm install -g @qwen-code/qwen-code

# 验证
qwen --version
which qwen
```

> 如果客户网络无 npmjs 出口，从 C4 内部 npm registry 装：
> ```bash
> npm install -g --registry https://npm.agent-platform.local @qwen-code/qwen-code
> ```

## 配置：指向 LiteLLM 网关

`~/.qwen/config.json`（或环境变量等效）：

```json
{
  "model": "qwen-coder-32b",
  "baseUrl": "http://localhost:4000/v1",
  "apiKey": "sk-eval-master-key",
  "approval": "yolo",
  "maxIterations": 20
}
```

环境变量等效（runner 用这个，避免污染 home dir）：

```bash
export OPENAI_BASE_URL="http://localhost:4000/v1"
export OPENAI_API_KEY="sk-eval-master-key"
export QWEN_MODEL="qwen-coder-32b"
```

## Hello-world

```bash
qwen --model qwen-coder-32b -p "用 Python 写一行: print('hello from qcoder')"
```

期望：返回的代码包含 `print('hello from qcoder')` 字样。

## 健康检查

```bash
# 1. CLI 可用
qwen --version  || { echo "qcoder not installed"; exit 1; }
# 2. 网关可达
curl -s "${OPENAI_BASE_URL}/models" -H "Authorization: Bearer ${OPENAI_API_KEY}" \
  | jq -r '.data[].id' | grep -q "$QWEN_MODEL" \
  || { echo "model $QWEN_MODEL not served by gateway"; exit 1; }
# 3. 实跑 1 prompt
qwen --model "$QWEN_MODEL" -p "echo only the word: ok" 2>&1 | grep -i "ok"
```

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `command not found: qwen` | npm 全局路径不在 PATH | `export PATH="$(npm prefix -g)/bin:$PATH"` |
| 403 from gateway | API key 错或网关未启 Anthropic 透传 | 用 OpenAI 协议端 `/v1`，不要走 `/anthropic` |
| 超时 | 后端 LLM 推理慢 / GPU OOM | 降并发或换更小 quantization |
| 中文乱码 | 终端非 UTF-8 | `export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8` |

## 在 agent-platform runner 里调用

参考 `eval/runner.py` 中 `AGENTS["qcoder"]` 项。
