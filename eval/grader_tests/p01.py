"""Grader for p01: LRU cache, thread-safe, generic, capacity-bound."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import pytest

CACHE_FILE = Path("cache.py")


def _load_cache_class():
    spec = importlib.util.spec_from_file_location("user_cache", CACHE_FILE)
    if spec is None or spec.loader is None:
        pytest.fail("cache.py not importable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    candidates = [
        getattr(mod, n)
        for n in dir(mod)
        if "cache" in n.lower() and isinstance(getattr(mod, n), type)
    ]
    if not candidates:
        pytest.fail("no class containing 'cache' in cache.py")
    return candidates[0]


def test_cache_file_exists():
    assert CACHE_FILE.exists(), "cache.py not generated"


def test_basic_put_get():
    cls_obj = _load_cache_class()
    c = cls_obj(capacity=3)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    assert c.get("b") == 2


def test_eviction_when_full():
    cls_obj = _load_cache_class()
    c = cls_obj(capacity=2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)  # evicts "a"
    assert c.get("a") in (None,)  # may return None or KeyError-friendly default
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_lru_order_updates_on_get():
    cls_obj = _load_cache_class()
    c = cls_obj(capacity=2)
    c.put("a", 1)
    c.put("b", 2)
    _ = c.get("a")  # touch "a" — now b is LRU
    c.put("c", 3)  # evicts "b"
    assert c.get("a") == 1
    assert c.get("b") in (None,)


def test_len_method():
    cls_obj = _load_cache_class()
    c = cls_obj(capacity=3)
    assert len(c) == 0
    c.put("a", 1)
    c.put("b", 2)
    assert len(c) == 2


def test_clear_method():
    cls_obj = _load_cache_class()
    c = cls_obj(capacity=3)
    c.put("a", 1)
    c.clear()
    assert len(c) == 0


def test_thread_safety_no_race_no_crash():
    cls_obj = _load_cache_class()
    c = cls_obj(capacity=100)
    errors = []

    def worker(start: int):
        try:
            for i in range(start, start + 200):
                c.put(f"k{i % 50}", i)
                c.get(f"k{i % 50}")
        except Exception as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(s * 200,)) for s in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"thread safety violations: {errors}"
    assert len(c) <= 100, "capacity exceeded under contention"
