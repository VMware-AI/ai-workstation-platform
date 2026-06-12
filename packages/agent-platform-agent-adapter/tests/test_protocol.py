"""Protocol contract tests — every registered adapter must satisfy."""

from __future__ import annotations

import dataclasses

# Side-effect import: registers the adapters into REGISTRY (PR #222 review
# HIGH — the F401 autofix deleted this line; isolation-run regression guard).
import agent_platform_agent_adapter.adapters  # noqa: F401
import pytest
from agent_platform_agent_adapter import (
    REGISTRY,
    AgentBackend,
    AgentLaunchSpec,
    UserContext,
    list_agents,
)


@pytest.fixture
def ctx(tmp_path) -> UserContext:
    return UserContext(
        user_id="alice",
        workspace=tmp_path,
        api_key="sk-test",
        llm_gateway_url="http://localhost:4000",
    )


def test_adapters_registered():
    names = list_agents()
    assert set(names) >= {"xiaoguai", "goose", "qcoder", "claude-code"}


@pytest.mark.parametrize("name", ["xiaoguai", "qcoder", "goose", "claude-code"])
def test_each_adapter_satisfies_protocol(name):
    agent = REGISTRY[name]
    assert isinstance(agent, AgentBackend)
    assert agent.name == name
    assert isinstance(agent.default_model, str) and agent.default_model


@pytest.mark.parametrize("name", ["xiaoguai", "qcoder", "goose", "claude-code"])
def test_launch_spec_returns_frozen_dataclass(name, ctx):
    agent = REGISTRY[name]
    spec = agent.launch_spec(ctx, "say hi")
    assert isinstance(spec, AgentLaunchSpec)
    assert spec.name == name
    assert spec.binary
    assert spec.timeout_s > 0
    assert spec.cwd == ctx.workspace
    # frozen — mutation must fail
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.binary = "x"  # type: ignore[misc]


def test_qcoder_uses_openai_protocol(ctx):
    spec = REGISTRY["qcoder"].launch_spec(ctx, "p")
    assert spec.env["OPENAI_BASE_URL"].endswith("/v1")
    assert spec.env["OPENAI_API_KEY"] == ctx.api_key
    assert "ANTHROPIC_BASE_URL" not in spec.env


def test_xiaoguai_uses_openai_protocol(ctx):
    spec = REGISTRY["xiaoguai"].launch_spec(ctx, "p")
    # Verified upstream env: OPENAI_BASE_URL (with /v1) + OPENAI_API_KEY.
    assert spec.env["OPENAI_BASE_URL"] == f"{ctx.llm_gateway_url}/v1"
    assert spec.env["OPENAI_API_KEY"] == ctx.api_key
    assert "ANTHROPIC_BASE_URL" not in spec.env
    # One-shot via `xiaoguai chat --prompt`, not the serve web server.
    assert spec.args[0] == "chat" and "--prompt" in spec.args


def test_goose_uses_openai_host(ctx):
    spec = REGISTRY["goose"].launch_spec(ctx, "p")
    assert spec.env["OPENAI_HOST"] == ctx.llm_gateway_url
    assert spec.env["GOOSE_PROVIDER"] == "openai"


def test_claude_code_uses_anthropic_protocol(ctx):
    spec = REGISTRY["claude-code"].launch_spec(ctx, "p")
    assert spec.env["ANTHROPIC_BASE_URL"].endswith("/anthropic")
    assert "OPENAI_BASE_URL" not in spec.env


@pytest.mark.parametrize("name", ["qcoder", "xiaoguai"])
def test_model_override_propagates(name, ctx):
    overridden = UserContext(
        user_id=ctx.user_id,
        workspace=ctx.workspace,
        api_key=ctx.api_key,
        llm_gateway_url=ctx.llm_gateway_url,
        model="my-custom-model",
    )
    spec = REGISTRY[name].launch_spec(overridden, "p")
    assert "my-custom-model" in spec.args


def test_health_check_returns_tuple(ctx):
    for name in ("xiaoguai", "qcoder", "goose", "claude-code"):
        ok, msg = REGISTRY[name].health_check(ctx)
        assert isinstance(ok, bool)
        assert isinstance(msg, str) and msg


def test_workspace_passed_as_cwd(tmp_path):
    ws = tmp_path / "my-workspace"
    ws.mkdir()
    ctx = UserContext(user_id="x", workspace=ws, api_key="k", llm_gateway_url="http://x")
    spec = REGISTRY["qcoder"].launch_spec(ctx, "p")
    assert spec.cwd == ws


def test_user_context_is_frozen(tmp_path):
    ctx = UserContext(user_id="x", workspace=tmp_path, api_key="k", llm_gateway_url="http://x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.user_id = "y"  # type: ignore[misc]


def test_launch_spec_args_is_tuple(ctx):
    spec = REGISTRY["qcoder"].launch_spec(ctx, "p")
    assert isinstance(spec.args, tuple)


# --- prompt delivery (#355 item 2): stdin for CLIs that support it ----------

_SECRET_PROMPT = "TOP-SECRET-PROMPT-do-not-leak-to-argv"


def test_claude_reads_prompt_from_stdin(ctx):
    spec = REGISTRY["claude-code"].launch_spec(ctx, _SECRET_PROMPT)
    # `-p` with no inline prompt arg → Claude Code reads stdin, so the prompt
    # stays off argv (ps/proc leak + ARG_MAX).
    assert "-p" in spec.args
    assert spec.stdin_data == _SECRET_PROMPT
    assert _SECRET_PROMPT not in spec.args


@pytest.mark.parametrize("name", ["goose", "qcoder", "xiaoguai"])
def test_stdin_incapable_adapters_keep_prompt_on_argv(name, ctx):
    """goose / qwen / xiaoguai have no verified stdin prompt mode, so the
    documented fallback is prompt-on-argv with stdin_data left None (runner
    feeds DEVNULL). goose uses `run -t <prompt>` per eval/runner.py."""
    spec = REGISTRY[name].launch_spec(ctx, _SECRET_PROMPT)
    assert spec.stdin_data is None
    assert _SECRET_PROMPT in spec.args
