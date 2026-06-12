"""Runner tests — uses /usr/bin/true / /usr/bin/false to fake agents."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

import pytest
from agent_platform_agent_adapter.protocol import AgentLaunchSpec, UserContext
from agent_platform_agent_adapter.runner import run_agent
from agent_platform_agent_adapter.telemetry import TelemetryEvent

TRUE = shutil.which("true") or "/usr/bin/true"
FALSE = shutil.which("false") or "/usr/bin/false"


@dataclass
class FakeAgent:
    name: str = "fake"
    default_model: str = "fake-model"
    binary: str = TRUE
    args: tuple = ()
    extra_env: dict | None = None
    timeout_s: int = 30
    stdin_data: str | None = None

    def launch_spec(self, ctx, prompt):
        return AgentLaunchSpec(
            name=self.name,
            binary=self.binary,
            args=self.args,
            env=self.extra_env or {},
            cwd=ctx.workspace,
            timeout_s=self.timeout_s,
            stdin_data=self.stdin_data,
        )

    def health_check(self, ctx):
        return True, "ok"


class CollectingShim:
    def __init__(self):
        self.events: list[TelemetryEvent] = []

    def send(self, e):
        self.events.append(e)

    def flush(self):
        pass


@pytest.fixture
def ctx(tmp_path):
    return UserContext(user_id="alice", workspace=tmp_path, api_key="k", llm_gateway_url="http://x")


def test_success_returns_rc_zero(ctx):
    shim = CollectingShim()
    r = run_agent(FakeAgent(binary=TRUE), ctx, "hi", shim=shim)
    assert r.returncode == 0
    assert not r.timed_out
    assert r.error is None
    assert len(shim.events) == 1
    assert shim.events[0].success is True


def test_failure_returns_nonzero(ctx):
    shim = CollectingShim()
    r = run_agent(FakeAgent(binary=FALSE), ctx, "hi", shim=shim)
    assert r.returncode != 0
    assert shim.events[0].success is False


def test_missing_binary_is_graceful(ctx):
    shim = CollectingShim()
    r = run_agent(FakeAgent(binary="/nonexistent/path/zzz"), ctx, "hi", shim=shim)
    assert r.returncode == -2
    assert r.error and "not found" in r.error
    assert shim.events[0].error


def test_timeout_is_graceful(ctx):
    # Use /bin/sleep 10, timeout 1s
    sleep = shutil.which("sleep")
    if not sleep:
        pytest.skip("sleep not found")
    shim = CollectingShim()
    r = run_agent(
        FakeAgent(binary=sleep, args=("10",), timeout_s=1),
        ctx,
        "hi",
        shim=shim,
    )
    assert r.timed_out
    assert r.error and "timeout" in r.error
    assert shim.events[0].success is False


def test_env_allowlist_filters_host_but_keeps_spec_env(ctx, monkeypatch):
    # SEC-15: a non-allowlisted host var (another service's secret) must NOT
    # leak to the agent; an allowlisted system var (LANG) passes through; the
    # curated spec.env (CUSTOM) always passes.
    monkeypatch.setenv("SECRET_FROM_OTHER_SERVICE", "leaked")
    monkeypatch.setenv("LANG", "C.UTF-8")
    shim = CollectingShim()
    agent = FakeAgent(
        binary=sys.executable,
        args=(
            "-c",
            "import os; print(os.environ.get('SECRET_FROM_OTHER_SERVICE'), "
            "os.environ.get('LANG'), os.environ.get('CUSTOM'))",
        ),
        extra_env={"CUSTOM": "from_agent"},
    )
    r = run_agent(agent, ctx, "hi", shim=shim)
    assert r.returncode == 0
    # non-allowlisted host secret is filtered out (Python prints "None")
    assert "leaked" not in r.stdout
    # allowlisted system var + curated spec.env both reach the agent
    assert "C.UTF-8" in r.stdout
    assert "from_agent" in r.stdout


def test_shim_optional_defaults_to_null(ctx):
    # No shim provided — must not crash
    r = run_agent(FakeAgent(binary=TRUE), ctx, "hi")
    assert r.returncode == 0


def test_telemetry_emitted_on_failure(ctx):
    shim = CollectingShim()
    run_agent(FakeAgent(binary="/nonexistent/zzz"), ctx, "hi", shim=shim)
    assert len(shim.events) == 1
    assert shim.events[0].success is False


def test_stdin_data_is_piped_to_child(ctx):
    # When spec.stdin_data is set, the runner must write it to the child's
    # stdin (so adapters can keep the prompt off argv) — #355 item 2.
    agent = FakeAgent(
        binary=sys.executable,
        args=("-c", "import sys; sys.stdout.write(sys.stdin.read())"),
        stdin_data="prompt-from-stdin",
    )
    r = run_agent(agent, ctx, "ignored", shim=CollectingShim())
    assert r.returncode == 0
    assert r.stdout == "prompt-from-stdin"


def test_no_stdin_data_feeds_empty_stdin(ctx):
    # With no stdin_data the child gets DEVNULL, never the inherited tty — a
    # stdin read returns EOF immediately instead of blocking/hanging.
    agent = FakeAgent(
        binary=sys.executable,
        args=("-c", "import sys; sys.stdout.write(repr(sys.stdin.read()))"),
    )
    r = run_agent(agent, ctx, "ignored", shim=CollectingShim())
    assert r.returncode == 0
    assert r.stdout == "''"
