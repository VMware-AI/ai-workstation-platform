"""Grader for p17: count_lang.py -> chinese=X english=Y digit=Z punct=W space=V."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


def test_script_exists():
    assert Path("count_lang.py").exists()


def test_runs_and_prints_expected_format():
    if not Path("mixed.txt").exists():
        Path("mixed.txt").write_text("Hello 你好 World, this is 测试 123。\n", encoding="utf-8")
    proc = subprocess.run(
        ["python3", "count_lang.py"],  # noqa: S607  # python3 from PATH is the contract
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        pytest.fail(f"script exited {proc.returncode}\nstderr:\n{proc.stderr}")
    out = proc.stdout.strip()
    # 接受 5 个 key=value 顺序灵活
    fields = dict(re.findall(r"(\w+)\s*=\s*(\d+)", out))
    required = {"chinese", "english", "digit", "punct", "space"}
    assert required.issubset(fields.keys()), f"missing keys; got {fields}"
    assert int(fields["chinese"]) >= 4, "应至少 4 个中文字 (你好测试)"
    assert int(fields["digit"]) >= 3, "应至少 3 个数字 (123)"
