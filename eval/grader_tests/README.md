# grader_tests/

每个 grader=code-runnable 的 prompt 必须配一个同名 pytest 文件：

```
grader_tests/p01.py     # 测试 cache.py 的 LRU 行为
grader_tests/p02.py     # 测试 sizeparser.py
...
```

runner 在 grader 阶段执行：

```bash
cd <workdir>
pytest -x --tb=short -q _grader_<id>.py
```

返回 exit=0 即满分。

## 写 grader test 的规则

1. 只测 prompt 描述的行为，不测风格 / 命名 / 注释
2. 测试要有"行为多样性"覆盖（边界 / 异常 / 正常）
3. 用 importlib 而非 from xxx import，让被测文件名/类名变化也能跑（agent 可能起不同名字）
4. 总执行时间 ≤ 10 秒
5. 不依赖外部网络
