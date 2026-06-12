"""Grader for p03: @retry decorator with backoff."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest


def _load_retry():
    spec = importlib.util.spec_from_file_location("user_retry", Path("retry.py"))
    if spec is None or spec.loader is None:
        pytest.fail("retry.py not importable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "retry", None)
    if fn is None:
        pytest.fail("function `retry` not found")
    return fn


def test_success_first_try_calls_once():
    retry = _load_retry()
    counter = {"n": 0}

    @retry(times=3, backoff=0.01)
    def ok():
        counter["n"] += 1
        return "ok"

    assert ok() == "ok"
    assert counter["n"] == 1


def test_retries_then_succeeds():
    retry = _load_retry()
    counter = {"n": 0}

    @retry(times=3, backoff=0.01)
    def flaky():
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("not yet")
        return "yay"

    assert flaky() == "yay"
    assert counter["n"] == 3


def test_gives_up_after_max():
    retry = _load_retry()
    counter = {"n": 0}

    @retry(times=2, backoff=0.01)
    def always_fail():
        counter["n"] += 1
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        always_fail()
    # times=2 = 2 attempts total (or 3 — accept either common interpretation)
    assert counter["n"] in (2, 3)


def test_exponential_backoff_delays():
    retry = _load_retry()
    timestamps: list[float] = []

    @retry(times=4, backoff=2.0)
    def fail():
        timestamps.append(time.perf_counter())
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        fail()

    if len(timestamps) < 3:
        pytest.skip("need ≥3 attempts to verify exponential backoff")
    gap1 = timestamps[1] - timestamps[0]
    gap2 = timestamps[2] - timestamps[1]
    assert gap2 > gap1 * 1.2, f"expected exponential growth, got {gap1=} {gap2=}"
