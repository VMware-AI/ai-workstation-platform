"""Grader for p02: parse_size('2.5GB') -> int bytes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load():
    spec = importlib.util.spec_from_file_location("user_size", Path("sizeparser.py"))
    if spec is None or spec.loader is None:
        pytest.fail("sizeparser.py not importable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "parse_size", None)
    if fn is None:
        pytest.fail("function `parse_size` not found")
    return fn


@pytest.mark.parametrize(
    "given,expected",
    [
        ("0B", 0),
        ("1B", 1),
        ("1KB", 1024),
        ("1MB", 1024**2),
        ("1GB", 1024**3),
        ("1TB", 1024**4),
        ("2.5GB", int(2.5 * 1024**3)),
        ("100 MB", 100 * 1024**2),
        ("100mb", 100 * 1024**2),  # 大小写不敏感
        ("3.0 tb", 3 * 1024**4),
    ],
)
def test_valid(given, expected):
    parse_size = _load()
    assert parse_size(given) == expected


@pytest.mark.parametrize("bad", ["", "abc", "10XB", "GB", "-5MB", "1.2.3GB"])
def test_invalid_raises_value_error(bad):
    parse_size = _load()
    with pytest.raises(ValueError):
        parse_size(bad)
