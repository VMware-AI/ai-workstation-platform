"""Graders for the M0.2 eval matrix.

Each grader takes:
  prompt_spec: dict (one entry from prompts/index.yaml)
  workdir:     pathlib.Path (agent worked here)
  proc:        subprocess.CompletedProcess (agent stdout/stderr/returncode)

and returns a float in [0.0, 1.0].
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from .code_runnable import grade_code_runnable
from .file_exists import grade_file_exists
from .llm_judge import grade_llm_judge

__all__ = ["grade"]


def grade(prompt_spec: dict, workdir: Path, proc: CompletedProcess) -> float:
    """Dispatch to the grader named in prompt_spec['grader']."""
    name = prompt_spec.get("grader", "exit-code-zero")
    if name == "exit-code-zero":
        return 1.0 if proc.returncode == 0 else 0.0
    if name == "file-exists":
        return grade_file_exists(prompt_spec, workdir)
    if name == "code-runnable":
        return grade_code_runnable(prompt_spec, workdir)
    if name == "llm-judge":
        return grade_llm_judge(prompt_spec, workdir, proc)
    raise ValueError(f"unknown grader: {name!r}")
