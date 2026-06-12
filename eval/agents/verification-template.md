# Agent hello-world 验收记录

> 关联 Issue: #6
> 目标: 三个 agent 均完成安装、版本确认、hello-world 输出和截图留档。

## 环境

| 字段 | 值 |
|---|---|
| 验证日期 | YYYY-MM-DD |
| 验证人 | |
| OS / 版本 | |
| Node.js 版本 | |
| LiteLLM / Gateway URL | |
| 后端模型 | |

## qcoder

| 检查项 | 结果 |
|---|---|
| `qwen --version` | |
| hello-world 命令 | `qwen --model qwen-coder-32b -p "用 Python 写一行: print('hello from qcoder')"` |
| 输出是否包含 `hello from qcoder` | □ 是 □ 否 |
| 截图 / 日志链接 | |

## Goose

| 检查项 | 结果 |
|---|---|
| `goose --version` | |
| hello-world 命令 | `goose run -t "用 Python 写一行: print('hello from goose')" --quiet` |
| 输出是否包含 `hello from goose` | □ 是 □ 否 |
| 截图 / 日志链接 | |

## Claude Code

| 检查项 | 结果 |
|---|---|
| `claude --version` | |
| hello-world 命令 | `claude -p "用 Python 写一行: print('hello from claude-code')"` |
| 输出是否包含 `hello from claude-code` | □ 是 □ 否 |
| `ANTHROPIC_BASE_URL` 验证链接 | |
| 截图 / 日志链接 | |

## 结论

- [ ] 三个 CLI 均可执行
- [ ] 三个 hello-world 均返回预期字符串
- [ ] 截图或日志已留档
- [ ] 已将验证版本回填到各 agent 的 `install.md`
