# 评测 Prompt 集

20 题 × 5 类别，配套 `0.2.8` runner + grader。

## 类别分布

| 类别 | 数量 | Grader 主用 | 验收维度 |
|---|---:|---|---|
| code-gen | 5 | code-runnable / file-exists | 代码可运行性、风格 |
| tool-single | 5 | exit-code-zero | tool-call 成功率、参数正确性 |
| tool-chain | 5 | exit-code-zero / file-exists | 工具序列合理性、最终产出 |
| i18n-code | 3 | code-runnable | 中文意图捕获 + 代码 |
| long-context | 2 | exit-code-zero / llm-judge | 长文 filter / 摘要质量 |

## Fixtures

`eval/fixtures/` 提供：
- `sample.json` — p06 测试用 ~10 行 JSON
- `users.csv` — p12 测试用 ~30 行
- `sales.json` — p14 测试用 ~20 transaction
- `mixed.txt` — p17 内联生成（setup 里 echo）
- `big_log.txt` — p19 ~5000 行合成 nginx log（用 `make-big-log.py` 生成）
- `spec.md` — p20 ~4000 字 fake spec

## 新增 Prompt 规则

1. 必须覆盖 5 类至少一类
2. setup 命令必须**幂等**（runner 每 iter 跑一次新 workdir）
3. grader 选 `code-runnable` 时必须配 `grader_tests/<id>.py`
4. prompt 用中文为主（客户大概率中文环境）
5. 引用 fixture 时用相对路径 `fixtures/xxx`，runner 会在 workdir 内暴露该目录
