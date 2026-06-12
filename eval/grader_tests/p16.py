"""Grader for p16: 中文姓名 -> 拼音首字母大写."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load():
    spec = importlib.util.spec_from_file_location("user_pinyin", Path("pinyin_initials.py"))
    if spec is None or spec.loader is None:
        pytest.fail("pinyin_initials.py not importable")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as e:
        pytest.skip(f"third-party dep missing: {e}")
    for name in ("name_to_initials", "to_initials", "convert", "pinyin_initials"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    pytest.fail("no callable found (tried name_to_initials/to_initials/convert/pinyin_initials)")


@pytest.mark.parametrize(
    "given,expected",
    [
        ("王小明", "WXM"),
        ("张三", "ZS"),
        ("李四", "LS"),
    ],
)
def test_single_char_surname(given, expected):
    assert _load()(given) == expected


@pytest.mark.parametrize(
    "given,expected_options",
    [
        # 多字姓允许两种合理写法：欧阳一彤 → OYYT 或 OYT
        ("欧阳一彤", ["OYYT", "OYT"]),
        ("司马懿", ["SMY"]),
    ],
)
def test_compound_surname(given, expected_options):
    assert _load()(given) in expected_options
