"""`agent` CLI — list / use / run / health.

Persists user's default agent in ~/.config/agent-platform/agent_default; survives reboot.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from . import adapters  # noqa: F401 — side effect: register the adapters
from .protocol import AgentBackend, UserContext
from .registry import get_agent, list_agents
from .runner import run_agent

# Preferred default agent on a fresh install (no agent_default file yet).
# Default-install set is xiaoguai (preferred) + goose.
PREFERRED_DEFAULT = "xiaoguai"


def _default_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    p = base / "agent-platform" / "agent_default"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_default() -> str:
    avail = list_agents()
    p = _default_file()
    if p.exists():
        name = p.read_text(encoding="utf-8").strip()
        if name in avail:
            return name
    # No (valid) saved default: prefer xiaoguai, else first registered.
    if PREFERRED_DEFAULT in avail:
        return PREFERRED_DEFAULT
    return avail[0] if avail else PREFERRED_DEFAULT


def _write_default(name: str) -> None:
    if name not in list_agents():
        raise click.BadParameter(f"unknown agent {name!r}. Available: {list_agents()}")
    _default_file().write_text(name, encoding="utf-8")


def _get_agent_or_fail(name: str) -> AgentBackend:
    """Translate the registry's KeyError into a clean CLI error.

    Keeps the registry's teaching "Available: [...]" text but renders it
    through click's error formatting instead of a raw traceback.
    """
    try:
        return get_agent(name)
    except KeyError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        raise click.BadParameter(str(detail)) from exc


def _require_api_key(ctx: UserContext) -> None:
    """Fail fast on a missing LLM API key instead of a deep 401 later.

    Without this, an empty key rides along until the third-party agent
    CLI hits the gateway and surfaces a confusing 401 from deep inside.
    """
    if not ctx.api_key:
        raise click.UsageError(
            "AGENT_PLATFORM_LLM_API_KEY is not set (or empty) — agents need it "
            "to reach the LLM gateway. Export it first, e.g.:\n"
            "  export AGENT_PLATFORM_LLM_API_KEY=<key issued by the platform console>"
        )


def _build_ctx(workspace: Path | None) -> UserContext:
    ws = workspace or Path(os.environ.get("AGENT_PLATFORM_WORKSPACE") or Path.home() / "workspace")
    ws.mkdir(parents=True, exist_ok=True)
    return UserContext(
        user_id=os.environ.get("AGENT_PLATFORM_USER", os.environ.get("USER", "unknown")),
        workspace=ws,
        api_key=os.environ.get("AGENT_PLATFORM_LLM_API_KEY", ""),
        llm_gateway_url=os.environ.get("AGENT_PLATFORM_LLM_GATEWAY_URL", "http://localhost:4000"),
    )


# === CLI ===


@click.group(
    help=(
        "Agent Platform agent dispatcher — "
        "switch among agents (default: xiaoguai / Goose; also qcoder / Claude Code)."
    ),
)
@click.version_option()
def main() -> None:
    pass


@main.command("list", help="List installed agents (* = current default)")
def cmd_list() -> None:
    default = _read_default()
    for name in list_agents():
        marker = "* " if name == default else "  "
        click.echo(f"{marker}{name}")


@main.command("use", help="Set default agent")
@click.argument("name")
def cmd_use(name: str) -> None:
    _write_default(name)
    click.echo(f"default agent → {name}")


@main.command("health", help="Check the current (or named) agent is reachable")
@click.option("--name", default=None, help="agent name (default: current)")
def cmd_health(name: str | None) -> None:
    name = name or _read_default()
    agent = _get_agent_or_fail(name)
    ctx = _build_ctx(workspace=None)
    ok, msg = agent.health_check(ctx)
    click.echo(f"{name}: {'OK' if ok else 'FAIL'} — {msg}")
    sys.exit(0 if ok else 1)


@main.command("run", help="Run a prompt with the default (or --with) agent")
@click.argument("prompt", nargs=-1, required=True)
@click.option("--with", "with_agent", default=None, help="one-off override of the agent")
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--quiet", is_flag=True, help="suppress stderr, only print stdout")
def cmd_run(
    prompt: tuple[str, ...], with_agent: str | None, workspace: Path | None, quiet: bool
) -> None:
    name = with_agent or _read_default()
    agent = _get_agent_or_fail(name)
    ctx = _build_ctx(workspace=workspace)
    _require_api_key(ctx)
    text = " ".join(prompt)
    result = run_agent(agent, ctx, text)

    sys.stdout.write(result.stdout)
    if not quiet:
        sys.stderr.write(result.stderr)
    if result.error:
        click.echo(f"\n[error] {result.error}", err=True)
    sys.exit(result.returncode if result.returncode >= 0 else 1)


@main.command(
    "chat",
    help="Interactive REPL: each line is sent to the current (or --with) agent; Ctrl-D exits.",
)
@click.option("--with", "with_agent", default=None, help="one-off override of the agent")
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), default=None)
def cmd_chat(with_agent: str | None, workspace: Path | None) -> None:
    name = with_agent or _read_default()
    agent = _get_agent_or_fail(name)
    ctx = _build_ctx(workspace=workspace)
    _require_api_key(ctx)
    # Reads until EOF — works for both an interactive TTY and a piped stdin.
    # Each non-blank line is one prompt; the agent stays fixed for the session,
    # so this is independent of which agents the M0.2 eval selects.
    for raw in click.get_text_stream("stdin"):
        prompt = raw.strip()
        if not prompt:
            continue
        result = run_agent(agent, ctx, prompt)
        sys.stdout.write(result.stdout)
        if not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
        if result.error:
            click.echo(f"[error] {result.error}", err=True)


if __name__ == "__main__":
    main()
