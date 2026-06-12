"""M0.2 评测 runner — 跑 (agent × LLM × prompt × iter) 矩阵。

用法：
  # 跑全量矩阵（9 组合 × 20 prompt × 3 iter）
  uv run python eval/runner.py

  # 只跑一个组合
  uv run python eval/runner.py --agent qcoder --llm qwen-coder-32b

  # 限定 prompt 子集
  uv run python eval/runner.py --prompts p01,p02,p06

  # 限定迭代次数
  uv run python eval/runner.py --iterations 1

  # dry-run 看会跑什么但不执行
  uv run python eval/runner.py --dry-run

环境变量（adapter 用）：
  AGENT_PLATFORM_LLM_GATEWAY_URL  默认 http://localhost:4000
  AGENT_PLATFORM_LLM_API_KEY      默认 sk-eval-master-key

输出：
  eval/results/<unix_ts>.csv
  eval/runs/<agent>-<llm>-<prompt>-it<n>/{stdout.log, stderr.log, <artifacts>}
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml  # type: ignore[import-not-found]
from grader import grade

ROOT = Path(__file__).resolve().parent
PROMPTS_FILE = ROOT / "prompts" / "index.yaml"
FIXTURES_DIR = ROOT / "fixtures"
RESULTS_DIR = ROOT / "results"
RUNS_DIR = ROOT / "runs"

GATEWAY_URL = os.environ.get("AGENT_PLATFORM_LLM_GATEWAY_URL", "http://localhost:4000")
API_KEY = os.environ.get("AGENT_PLATFORM_LLM_API_KEY", "sk-eval-master-key")

DEFAULT_TIMEOUT_S = int(os.environ.get("AGENT_PLATFORM_AGENT_TIMEOUT_S", "300"))

# Agent launchers: each returns the argv + env for subprocess.run.
# Real adapters live in agent-platform-agent-adapter; here we shell out to whichever CLI
# is on PATH. The runner does NOT install agents — caller must.

AGENTS: dict[str, dict] = {
    "qcoder": {
        "cmd": lambda model, prompt: ["qwen", "--model", model, "-p", prompt],
        "env": lambda model: {
            "OPENAI_BASE_URL": f"{GATEWAY_URL}/v1",
            "OPENAI_API_KEY": API_KEY,
        },
    },
    "goose": {
        "cmd": lambda model, prompt: ["goose", "run", "-t", prompt, "--quiet"],
        # env needs the model (goose selects it via GOOSE_MODEL, not argv) —
        # all env lambdas take it for a uniform call shape (F821 real bug:
        # this one previously closed over an undefined `model`).
        "env": lambda model: {
            "GOOSE_PROVIDER": "openai",
            "OPENAI_HOST": GATEWAY_URL,
            "OPENAI_API_KEY": API_KEY,
            "GOOSE_MODEL": model,
        },
    },
    "claude-code": {
        "cmd": lambda model, prompt: ["claude", "-p", prompt, "--model", model],
        "env": lambda model: {
            "ANTHROPIC_BASE_URL": f"{GATEWAY_URL}/anthropic",
            "ANTHROPIC_API_KEY": API_KEY,
        },
    },
}

DEFAULT_LLMS = ["qwen-coder-32b", "deepseek-coder-v2-lite", "llama-3.3-70b"]


@dataclass(frozen=True)
class RunResult:
    agent: str
    llm: str
    prompt_id: str
    iteration: int
    duration_s: float
    exit_code: int
    grader_score: float
    workdir: str
    stdout_bytes: int
    stderr_bytes: int
    timeout: bool = False
    error: str = ""


def load_prompts() -> list[dict]:
    with PROMPTS_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["prompts"]


def prepare_workdir(agent: str, llm: str, prompt_id: str, iteration: int) -> Path:
    wd = RUNS_DIR / f"{agent}-{llm}-{prompt_id}-it{iteration}"
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    return wd


def run_setup_steps(prompt_spec: dict, workdir: Path) -> None:
    for cmd in prompt_spec.get("setup") or []:
        subprocess.run(cmd, shell=True, cwd=workdir, check=True)  # noqa: S602 (controlled input)


def run_one(agent: str, llm: str, prompt_spec: dict, iteration: int) -> RunResult:
    wd = prepare_workdir(agent, llm, prompt_spec["id"], iteration)

    # Make fixtures accessible from every run directory without copying large files.
    fixtures_link = wd / "fixtures"
    if not fixtures_link.exists():
        fixtures_link.symlink_to(FIXTURES_DIR)

    try:
        run_setup_steps(prompt_spec, wd)
    except subprocess.CalledProcessError as e:
        return RunResult(
            agent=agent,
            llm=llm,
            prompt_id=prompt_spec["id"],
            iteration=iteration,
            duration_s=0.0,
            exit_code=-1,
            grader_score=0.0,
            workdir=str(wd),
            stdout_bytes=0,
            stderr_bytes=0,
            error=f"setup failed: {e}",
        )

    spec = AGENTS[agent]
    cmd = spec["cmd"](llm, prompt_spec["prompt"])
    env = {**os.environ, **spec["env"](llm)}

    t0 = time.perf_counter()
    timeout = False
    error = ""
    try:
        proc = subprocess.run(  # noqa: S603 (controlled)
            cmd,
            cwd=wd,
            env=env,
            capture_output=True,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        timeout = True
        duration = DEFAULT_TIMEOUT_S
        proc = subprocess.CompletedProcess(
            args=cmd, returncode=-9, stdout=e.stdout or b"", stderr=e.stderr or b""
        )
    except FileNotFoundError as e:
        return RunResult(
            agent=agent,
            llm=llm,
            prompt_id=prompt_spec["id"],
            iteration=iteration,
            duration_s=0.0,
            exit_code=-2,
            grader_score=0.0,
            workdir=str(wd),
            stdout_bytes=0,
            stderr_bytes=0,
            error=f"binary not found: {e}",
        )
    else:
        duration = time.perf_counter() - t0

    (wd / "stdout.log").write_bytes(proc.stdout or b"")
    (wd / "stderr.log").write_bytes(proc.stderr or b"")

    try:
        score = grade(prompt_spec, wd, proc)
    except Exception as e:
        score = 0.0
        error = error or f"grader error: {type(e).__name__}: {e}"

    return RunResult(
        agent=agent,
        llm=llm,
        prompt_id=prompt_spec["id"],
        iteration=iteration,
        duration_s=round(duration, 3),
        exit_code=proc.returncode,
        grader_score=round(score, 3),
        workdir=str(wd),
        stdout_bytes=len(proc.stdout or b""),
        stderr_bytes=len(proc.stderr or b""),
        timeout=timeout,
        error=error,
    )


def write_csv(results: list[RunResult]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{int(time.time())}.csv"
    fields = list(asdict(results[0]).keys()) if results else list(RunResult.__dataclass_fields__)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run M0.2 agent × LLM eval matrix")
    ap.add_argument(
        "--agent",
        action="append",
        choices=list(AGENTS),
        help="restrict to one or more agents (repeatable)",
    )
    ap.add_argument("--llm", action="append", help="restrict to one or more LLMs")
    ap.add_argument("--prompts", help="comma-separated prompt IDs (e.g. p01,p02)")
    ap.add_argument(
        "--iterations", type=int, default=3, help="iterations per (agent, llm, prompt) [default 3]"
    )
    ap.add_argument("--dry-run", action="store_true", help="print plan, don't execute")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    prompts = load_prompts()
    if args.prompts:
        wanted = set(args.prompts.split(","))
        prompts = [p for p in prompts if p["id"] in wanted]
    if not prompts:
        print("no prompts matched", file=sys.stderr)
        return 2

    agents = args.agent or list(AGENTS)
    llms = args.llm or DEFAULT_LLMS

    total = len(agents) * len(llms) * len(prompts) * args.iterations
    print(
        f"plan: {len(agents)} agents × {len(llms)} llms × {len(prompts)} prompts "
        f"× {args.iterations} iter = {total} runs"
    )
    if args.dry_run:
        for a in agents:
            for m in llms:
                for p in prompts:
                    print(f"  would run: {a} | {m} | {p['id']}")
        return 0

    results: list[RunResult] = []
    n = 0
    for a in agents:
        for m in llms:
            for p in prompts:
                for it in range(args.iterations):
                    n += 1
                    print(f"[{n}/{total}] {a} | {m} | {p['id']} | it{it}", flush=True)
                    r = run_one(a, m, p, it)
                    results.append(r)
                    if r.error:
                        print(f"  ! {r.error}", file=sys.stderr)

    out = write_csv(results)
    print(f"\nwrote {out} ({len(results)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
