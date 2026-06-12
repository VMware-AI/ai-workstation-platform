"""Unit tests for runner (no real agent — uses /bin/true / /bin/false stubs)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runner

# These tests require prompts/index.yaml (delivered by PR #44, M0.2.7).
# When running on a branch without that file (e.g. before #44 merges),
# they skip cleanly instead of failing.
PROMPTS_AVAILABLE = runner.PROMPTS_FILE.exists()
needs_prompts = pytest.mark.skipif(
    not PROMPTS_AVAILABLE, reason="prompts/index.yaml not present (PR #44 not merged yet)"
)


@needs_prompts
def test_load_prompts_has_20():
    prompts = runner.load_prompts()
    assert len(prompts) == 20
    ids = {p["id"] for p in prompts}
    assert "p01" in ids and "p20" in ids


@needs_prompts
def test_load_prompts_all_have_required_fields():
    for p in runner.load_prompts():
        assert "id" in p
        assert "category" in p
        assert "prompt" in p
        assert "grader" in p


def test_parse_args_defaults():
    args = runner.parse_args([])
    assert args.iterations == 3
    assert args.agent is None
    assert args.llm is None
    assert args.prompts is None
    assert args.dry_run is False


def test_parse_args_restrict():
    args = runner.parse_args(
        [
            "--agent",
            "qcoder",
            "--llm",
            "qwen-coder-32b",
            "--prompts",
            "p01,p02",
            "--iterations",
            "1",
        ]
    )
    assert args.agent == ["qcoder"]
    assert args.llm == ["qwen-coder-32b"]
    assert args.prompts == "p01,p02"
    assert args.iterations == 1


@needs_prompts
def test_dry_run_returns_zero_no_subprocess(capsys):
    with patch("subprocess.run") as mocked:
        rc = runner.main(
            [
                "--dry-run",
                "--agent",
                "qcoder",
                "--llm",
                "qwen-coder-32b",
                "--prompts",
                "p10",
                "--iterations",
                "1",
            ]
        )
    assert rc == 0
    mocked.assert_not_called()
    captured = capsys.readouterr()
    assert "would run: qcoder | qwen-coder-32b | p10" in captured.out


@needs_prompts
def test_no_matching_prompts_exits_2():
    rc = runner.main(["--dry-run", "--prompts", "p99"])
    assert rc == 2


def test_missing_binary_returns_error_result(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "FIXTURES_DIR", tmp_path / "fixtures")
    (tmp_path / "fixtures").mkdir()
    spec = {
        "id": "p10",
        "category": "tool-single",
        "prompt": "do",
        "grader": "exit-code-zero",
        "setup": [],
    }
    # qcoder/goose/claude binaries unlikely to exist on CI
    r = runner.run_one("qcoder", "qwen-coder-32b", spec, iteration=0)
    # Either error or actually ran successfully — both fine, just no crash.
    assert r.agent == "qcoder"
    assert r.prompt_id == "p10"


def test_run_one_links_fixtures_inside_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "FIXTURES_DIR", tmp_path / "fixtures")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "sample.txt").write_text("ok", encoding="utf-8")

    spec = {
        "id": "pfixture",
        "category": "tool-single",
        "prompt": "do",
        "grader": "file-exists",
        "setup": ["cp fixtures/sample.txt copied.txt"],
        "expected_artifacts": ["copied.txt"],
    }

    monkeypatch.setitem(
        runner.AGENTS,
        "stub",
        {
            "cmd": lambda _model, _prompt: ["true"],
            "env": lambda model: {},
        },
    )

    r = runner.run_one("stub", "qwen-coder-32b", spec, iteration=0)

    assert r.error == ""
    assert r.exit_code == 0
    assert r.grader_score == 1.0
    assert (Path(r.workdir) / "fixtures").is_symlink()
    assert (Path(r.workdir) / "copied.txt").read_text(encoding="utf-8") == "ok"
