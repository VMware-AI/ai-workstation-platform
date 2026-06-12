"""Grader for p18: generate_username with collision suffix."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load():
    spec = importlib.util.spec_from_file_location("user_un", Path("username_gen.py"))
    if spec is None or spec.loader is None:
        pytest.fail("username_gen.py not importable")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as e:
        pytest.skip(f"third-party dep missing: {e}")
    fn = getattr(mod, "generate_username", None)
    if fn is None:
        pytest.fail("function generate_username not found")
    return fn


def test_basic():
    fn = _load()
    assert fn("张三", set()) == "zhangsan"


def test_collision_appends_2():
    fn = _load()
    assert fn("张三", {"zhangsan"}) == "zhangsan2"


def test_collision_appends_3():
    fn = _load()
    assert fn("张三", {"zhangsan", "zhangsan2"}) == "zhangsan3"


def test_different_names_no_clash():
    fn = _load()
    assert fn("李四", set()) == "lisi"
