"""Grader: ask a neutral LLM to score (0-10) the agent output."""

from __future__ import annotations

import os
from pathlib import Path
from subprocess import CompletedProcess

JUDGE_BASE_URL = os.environ.get("AGENT_PLATFORM_JUDGE_BASE_URL", "http://localhost:4000/v1")
JUDGE_API_KEY = os.environ.get("AGENT_PLATFORM_JUDGE_API_KEY", "sk-eval-master-key")
JUDGE_MODEL = os.environ.get("AGENT_PLATFORM_JUDGE_MODEL", "qwen-coder-32b")
JUDGE_TIMEOUT_S = int(os.environ.get("AGENT_PLATFORM_JUDGE_TIMEOUT_S", "30"))


def grade_llm_judge(prompt_spec: dict, workdir: Path, proc: CompletedProcess) -> float:
    """Returns 0.0–1.0. Falls back to 0.0 if openai client unavailable."""
    try:
        import openai  # type: ignore[import-not-found]
    except ImportError:
        return 0.0

    out_snippet = (proc.stdout or "")[:4000]
    artifacts_summary = []
    for a in prompt_spec.get("expected_artifacts") or []:
        f = workdir / a
        if f.exists():
            try:
                artifacts_summary.append(f"--- {a} ---\n{f.read_text(errors='replace')[:2000]}")
            except OSError:
                artifacts_summary.append(f"--- {a} --- (unreadable)")
    artifacts_text = "\n\n".join(artifacts_summary) if artifacts_summary else "(no artifacts)"

    judge_prompt = (
        "你是一个严格但公正的评委。请对 AI agent 的输出打分（0-10）。\n\n"
        f"## 任务\n{prompt_spec['prompt']}\n\n"
        f"## Agent stdout\n{out_snippet}\n\n"
        f"## Agent 产物\n{artifacts_text}\n\n"
        "评分标准：\n"
        "1) 是否完成任务（最重要）\n"
        "2) 中文表达自然度\n"
        "3) 工具调用合理性（如适用）\n\n"
        "只回一个 0-10 的整数（不要其他文字）。"
    )

    client = openai.OpenAI(base_url=JUDGE_BASE_URL, api_key=JUDGE_API_KEY, timeout=JUDGE_TIMEOUT_S)
    try:
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": judge_prompt}],
            max_tokens=10,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        score = float("".join(c for c in raw.split()[0] if c.isdigit() or c == "."))
    except Exception:
        return 0.0
    return max(0.0, min(1.0, score / 10.0))
