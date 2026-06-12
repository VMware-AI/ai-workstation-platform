"""Grader: each expected_artifact must exist in workdir."""

from __future__ import annotations

from pathlib import Path


def grade_file_exists(prompt_spec: dict, workdir: Path) -> float:
    artifacts = prompt_spec.get("expected_artifacts") or []
    if not artifacts:
        return 0.0
    found = sum(1 for a in artifacts if (workdir / a).exists())
    return found / len(artifacts)
