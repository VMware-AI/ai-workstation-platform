"""Grader: run grader_tests/<id>.py with pytest inside workdir."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

GRADER_TESTS_DIR = Path(__file__).resolve().parent.parent / "grader_tests"


def grade_code_runnable(prompt_spec: dict, workdir: Path) -> float:
    pid = prompt_spec["id"]
    src = GRADER_TESTS_DIR / f"{pid}.py"
    if not src.exists():
        # No grader test authored — fall back to file-exists.
        from .file_exists import grade_file_exists

        return grade_file_exists(prompt_spec, workdir)
    if not shutil.which("pytest"):
        return 0.0
    dst = workdir / f"_grader_{pid}.py"
    shutil.copy(src, dst)
    try:
        proc = subprocess.run(  # noqa: S603  # grader deliberately executes candidate code
            ["pytest", "-x", "--tb=short", "-q", dst.name],  # noqa: S607  # python3 from PATH is the contract
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return 0.0
    if proc.returncode == 0:
        return 1.0
    # Partial credit: count "passed" tokens.
    out = proc.stdout + proc.stderr
    if " passed" in out and " failed" in out:
        try:
            passed = int(out.split(" passed", 1)[0].split()[-1])
            failed = int(out.split(" failed", 1)[0].split()[-1])
            total = passed + failed
            if total > 0:
                return passed / total
        except (ValueError, IndexError):
            pass
    return 0.0
