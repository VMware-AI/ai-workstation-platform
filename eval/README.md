# M0.2 评测套件

Agent × LLM 矩阵评测，配套 [`docs/plans/m0/0.2-agent-llm-eval-plan.md`](../docs/plans/m0/0.2-agent-llm-eval-plan.md)。

## 结构

```
eval/
├── prompts/           # 20 个评测 prompt (M0.2.7, PR #44)
│   ├── index.yaml
│   └── README.md
├── fixtures/          # 评测用数据
│   ├── sample.json users.csv sales.json spec.md
│   ├── big_log.txt   # 5000 行合成 nginx log
│   └── make-big-log.py
├── grader/            # 4 个 grader 实现 (M0.2.8, 本 PR)
│   ├── __init__.py
│   ├── file_exists.py
│   ├── code_runnable.py
│   └── llm_judge.py
├── grader_tests/      # code-runnable grader 的 pytest 文件
├── tests/             # runner + grader 的单元测试
├── runner.py          # 入口 (本 PR)
├── results/           # 评测产出 CSV (gitignored)
└── runs/              # 每次 run 的 workdir + 日志 (gitignored)
```

## 跑

```bash
# 前置：安装 eval 额外依赖（yaml + openai client）
uv sync --extra eval --dev

# 全量矩阵 (默认 3 agents × 3 LLMs × 20 prompts × 3 iter = 540 runs)
uv run python eval/runner.py

# 单组合
uv run python eval/runner.py --agent qcoder --llm qwen-coder-32b

# 子集 prompt
uv run python eval/runner.py --prompts p01,p06,p11 --iterations 1

# Dry run
uv run python eval/runner.py --dry-run
```

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `AGENT_PLATFORM_LLM_GATEWAY_URL` | `http://localhost:4000` | LiteLLM 网关 |
| `AGENT_PLATFORM_LLM_API_KEY` | `sk-eval-master-key` | API key |
| `AGENT_PLATFORM_AGENT_TIMEOUT_S` | `300` | 单次 agent 超时 |
| `AGENT_PLATFORM_JUDGE_MODEL` | `qwen-coder-32b` | LLM-judge 模型 |

## 单元测试

```bash
uv run pytest eval/tests/ -v
```

不调用真实 agent / LLM，纯逻辑测试。
