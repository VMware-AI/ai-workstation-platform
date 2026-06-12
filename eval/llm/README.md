# LLM 服务脚本

每个 LLM 一个子目录，含：
- `serve.sh` — 启 vLLM 服务（systemd 或前台都能跑）
- `vram.txt` — 实测显存占用（接手人回填）
- `notes.md` — quantization 选择 / 已知 bug / 备注

```
eval/llm/
├── qwen-coder-32b/      (LLM-A, M1 默认)
├── deepseek-coder-v2-lite/  (LLM-B, 备选)
└── llama-3.3-70b/       (LLM-C, GPU ≥ 48GB)
```

启动顺序：
```bash
bash eval/llm/qwen-coder-32b/serve.sh &     # 8001
bash eval/llm/deepseek-coder-v2-lite/serve.sh &  # 8002
# 三个 LLM 同时跑需要 ≥ 90GB 显存；通常串行评测
```

启动后 LiteLLM 网关（`eval/gateway/`）自动 route。
