"""Unit tests for graders (no LLM, no agent — pure logic)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from grader import grade
from grader.file_exists import grade_file_exists


def _proc(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout.encode(), stderr=b""
    )


def test_exit_code_zero_pass():
    spec = {"id": "x", "grader": "exit-code-zero"}
    assert grade(spec, Path("."), _proc(returncode=0)) == 1.0


def test_exit_code_zero_fail():
    spec = {"id": "x", "grader": "exit-code-zero"}
    assert grade(spec, Path("."), _proc(returncode=1)) == 0.0


def test_file_exists_all_present(tmp_path):
    (tmp_path / "a.txt").write_text("ok")
    (tmp_path / "b.txt").write_text("ok")
    spec = {"id": "x", "grader": "file-exists", "expected_artifacts": ["a.txt", "b.txt"]}
    assert grade(spec, tmp_path, _proc()) == 1.0


def test_file_exists_partial(tmp_path):
    (tmp_path / "a.txt").write_text("ok")
    spec = {"id": "x", "grader": "file-exists", "expected_artifacts": ["a.txt", "b.txt"]}
    assert grade_file_exists(spec, tmp_path) == 0.5


def test_file_exists_none(tmp_path):
    spec = {"id": "x", "grader": "file-exists", "expected_artifacts": ["a.txt"]}
    assert grade_file_exists(spec, tmp_path) == 0.0


def test_file_exists_empty_artifacts_returns_zero():
    spec = {"id": "x", "grader": "file-exists", "expected_artifacts": []}
    assert grade_file_exists(spec, Path(".")) == 0.0


def test_unknown_grader_raises():
    spec = {"id": "x", "grader": "no-such-grader"}
    with pytest.raises(ValueError):
        grade(spec, Path("."), _proc())


def test_default_grader_is_exit_code_zero():
    spec = {"id": "x"}  # no grader key
    assert grade(spec, Path("."), _proc(returncode=0)) == 1.0
