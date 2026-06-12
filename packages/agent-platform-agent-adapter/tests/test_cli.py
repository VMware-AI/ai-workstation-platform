"""CLI tests using click's CliRunner — no real agent exec."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_platform_agent_adapter.cli import main
from click.testing import CliRunner


@pytest.fixture
def isolated_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # run/chat fail fast on a missing LLM API key; give tests a dummy one
    # so dispatch tests stay deterministic regardless of the host env.
    monkeypatch.setenv("AGENT_PLATFORM_LLM_API_KEY", "test-key")
    yield tmp_path


def test_list_shows_registered_agents(isolated_cfg):
    r = CliRunner().invoke(main, ["list"])
    assert r.exit_code == 0
    # Default-install agents (xiaoguai + goose) plus the still-available ones.
    for n in ("xiaoguai", "goose", "qcoder", "claude-code"):
        assert n in r.output


def test_fresh_install_defaults_to_xiaoguai(isolated_cfg):
    # No agent_default file yet → the preferred default must be xiaoguai,
    # not the alphabetically-first agent (claude-code).
    out = CliRunner().invoke(main, ["list"]).output
    assert "* xiaoguai" in out


def test_use_sets_default(isolated_cfg):
    rn = CliRunner()
    assert rn.invoke(main, ["use", "goose"]).exit_code == 0
    out = rn.invoke(main, ["list"]).output
    assert "* goose" in out
    assert "  qcoder" in out


def test_use_unknown_agent_fails(isolated_cfg):
    r = CliRunner().invoke(main, ["use", "no-such-agent"])
    assert r.exit_code != 0
    assert "unknown" in r.output.lower() or "available" in r.output.lower()


def test_health_unknown_agent_fails(isolated_cfg):
    r = CliRunner().invoke(main, ["health", "--name", "no-such-agent"])
    assert r.exit_code != 0


def test_health_with_missing_binary_returns_nonzero(isolated_cfg, monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_WORKSPACE", raising=False)
    # Force the agent binary to be absent so the outcome is deterministic, not
    # "0 or 1 depending on whether the CI host happens to have qwen installed"
    # — the old `in (0, 1)` could never fail on the behavior it names (#H-10).
    monkeypatch.setattr("shutil.which", lambda _name: None)
    r = CliRunner().invoke(main, ["health", "--name", "qcoder"])
    assert r.exit_code == 1
    assert "qcoder" in r.output


def test_run_dispatches_to_default_agent(isolated_cfg):
    rn = CliRunner()
    rn.invoke(main, ["use", "qcoder"])  # set default explicitly
    with patch("agent_platform_agent_adapter.cli.run_agent") as m:
        from agent_platform_agent_adapter.runner import AgentRunResult

        m.return_value = AgentRunResult(
            agent="qcoder",
            model="qwen-coder-32b",
            returncode=0,
            duration_s=0.1,
            stdout="hello",
            stderr="",
            timed_out=False,
        )
        r = rn.invoke(main, ["run", "say", "hi"])
    assert r.exit_code == 0
    assert "hello" in r.output
    m.assert_called_once()
    called_agent = m.call_args[0][0]
    assert called_agent.name == "qcoder"


def test_run_with_overrides_default(isolated_cfg):
    with patch("agent_platform_agent_adapter.cli.run_agent") as m:
        from agent_platform_agent_adapter.runner import AgentRunResult

        m.return_value = AgentRunResult(
            agent="goose",
            model="qwen-coder-32b",
            returncode=0,
            duration_s=0.1,
            stdout="",
            stderr="",
            timed_out=False,
        )
        r = CliRunner().invoke(main, ["run", "--with", "goose", "hi"])
    assert r.exit_code == 0
    assert m.call_args[0][0].name == "goose"


def test_default_file_persists_across_invocations(isolated_cfg):
    rn = CliRunner()
    rn.invoke(main, ["use", "claude-code"])
    # New runner = new process simulated; default file should persist
    out = rn.invoke(main, ["list"]).output
    assert "* claude-code" in out


# --- chat REPL (PR-K1, menu-independent: operates on whatever the
#     default/--with agent is, so it makes no assumption about which
#     agents win the M0.2 eval) ---


def _ok_result(agent: str, stdout: str):
    from agent_platform_agent_adapter.runner import AgentRunResult

    return AgentRunResult(
        agent=agent,
        model="m",
        returncode=0,
        duration_s=0.1,
        stdout=stdout,
        stderr="",
        timed_out=False,
    )


def test_chat_dispatches_each_nonblank_line_to_default(isolated_cfg):
    rn = CliRunner()
    rn.invoke(main, ["use", "qcoder"])
    with patch("agent_platform_agent_adapter.cli.run_agent") as m:
        m.side_effect = lambda agent, ctx, text: _ok_result(agent.name, f"echo:{text}")
        r = rn.invoke(main, ["chat"], input="hello\nworld\n")
    assert r.exit_code == 0
    assert m.call_count == 2
    assert "echo:hello" in r.output
    assert "echo:world" in r.output
    assert m.call_args_list[0][0][0].name == "qcoder"


def test_chat_eof_exits_cleanly_with_no_input(isolated_cfg):
    r = CliRunner().invoke(main, ["chat"], input="")
    assert r.exit_code == 0


def test_chat_skips_blank_lines(isolated_cfg):
    rn = CliRunner()
    rn.invoke(main, ["use", "qcoder"])
    with patch("agent_platform_agent_adapter.cli.run_agent") as m:
        m.side_effect = lambda agent, ctx, text: _ok_result(agent.name, "ok")
        r = rn.invoke(main, ["chat"], input="\n  \nreal\n")
    assert r.exit_code == 0
    assert m.call_count == 1
    assert m.call_args[0][2] == "real"


def test_chat_with_overrides_default_agent(isolated_cfg):
    with patch("agent_platform_agent_adapter.cli.run_agent") as m:
        m.side_effect = lambda agent, ctx, text: _ok_result(agent.name, "hi")
        r = CliRunner().invoke(main, ["chat", "--with", "goose"], input="ping\n")
    assert r.exit_code == 0
    assert m.call_args[0][0].name == "goose"


def test_chat_unknown_with_agent_fails(isolated_cfg):
    r = CliRunner().invoke(main, ["chat", "--with", "no-such-agent"], input="x\n")
    assert r.exit_code != 0


# --- empty LLM API key fails fast (review item 8) ---


def test_run_fails_fast_on_missing_api_key(isolated_cfg, monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_LLM_API_KEY", raising=False)
    r = CliRunner().invoke(main, ["run", "hi"])
    assert r.exit_code != 0
    assert "AGENT_PLATFORM_LLM_API_KEY" in r.output


def test_run_fails_fast_on_empty_api_key(isolated_cfg, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_LLM_API_KEY", "")
    r = CliRunner().invoke(main, ["run", "hi"])
    assert r.exit_code != 0
    assert "AGENT_PLATFORM_LLM_API_KEY" in r.output


def test_chat_fails_fast_on_missing_api_key(isolated_cfg, monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_LLM_API_KEY", raising=False)
    r = CliRunner().invoke(main, ["chat"], input="hello\n")
    assert r.exit_code != 0
    assert "AGENT_PLATFORM_LLM_API_KEY" in r.output


# --- unknown agent → teaching CLI error, not a raw traceback (review item 9) ---


def test_run_unknown_with_agent_shows_available_list(isolated_cfg):
    r = CliRunner().invoke(main, ["run", "--with", "no-such-agent", "hi"])
    assert r.exit_code != 0
    assert "no-such-agent" in r.output
    assert "Available" in r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)


def test_health_unknown_agent_shows_available_list(isolated_cfg):
    r = CliRunner().invoke(main, ["health", "--name", "no-such-agent"])
    assert r.exit_code != 0
    assert "Available" in r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)


def test_chat_unknown_with_agent_shows_available_list(isolated_cfg):
    r = CliRunner().invoke(main, ["chat", "--with", "no-such-agent"], input="x\n")
    assert r.exit_code != 0
    assert "Available" in r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)
