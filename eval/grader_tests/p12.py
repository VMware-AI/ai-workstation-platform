"""Grader for p12: aggregate users.csv by department -> dept_count.csv."""

from __future__ import annotations

import csv
from pathlib import Path


def test_output_exists():
    assert Path("dept_count.csv").exists()


def test_aggregation_correct():
    with Path("dept_count.csv").open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    # 容忍 header 中英文 / 大小写
    by_dept: dict[str, int] = {}
    for r in rows:
        keys_low = {k.lower(): v for k, v in r.items()}
        dept = keys_low.get("department") or next(iter(r.values()))
        count_str = keys_low.get("count") or list(r.values())[1]
        by_dept[dept.strip()] = int(str(count_str).strip())
    expected = {
        "Engineering": 9,
        "Sales": 5,
        "Marketing": 3,
        "HR": 3,
    }
    assert by_dept == expected, f"got {by_dept}, expected {expected}"


def test_sorted_descending():
    with Path("dept_count.csv").open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    counts = []
    for r in rows:
        keys_low = {k.lower(): v for k, v in r.items()}
        c = keys_low.get("count") or list(r.values())[1]
        counts.append(int(str(c).strip()))
    assert counts == sorted(counts, reverse=True), f"not desc sorted: {counts}"
