"""Agent Platform gateway CLI — thin wrapper around the LiteLLM proxy.

The wrapper:

* validates the YAML config against platform invariants (refusing to
  start with a placeholder master_key, missing rpm/tpm, etc.)
* delegates to LiteLLM's own runtime for the actual serving

Why a wrapper instead of `litellm --config ...` directly: keeps the
fail-fast validator in one place, and gives operators a single
``agent-platform-llm-gateway start`` command that exits cleanly with a
human-readable error rather than a stack trace.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import typer
import yaml

from .config import GatewayConfigError, load_config, validate_config
from .secrets_bootstrap import (
    GatewaySecretsError,
    resolve_config_secrets,
    resolve_ingest_token_env,
)

app = typer.Typer(add_completion=False, help="agent-platform-llm-gateway control commands")
logger = logging.getLogger(__name__)


def _default_config_path() -> Path:
    env = os.environ.get("AGENT_PLATFORM_LLM_GATEWAY_CONFIG")
    if env:
        return Path(env)
    return Path("/etc/agent-platform-llm-gateway/config.yaml")


@app.command()
def validate(
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to gateway YAML. Defaults to $AGENT_PLATFORM_LLM_GATEWAY_CONFIG or /etc/...",
    ),
) -> None:
    """Validate the config file against platform invariants and exit."""
    path = config or _default_config_path()
    try:
        cfg = load_config(path)
        validate_config(cfg)
    except FileNotFoundError:
        typer.echo(f"config not found: {path}", err=True)
        raise typer.Exit(2) from None
    except GatewayConfigError as exc:
        typer.echo(f"invalid config: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"OK {path}")


@app.command()
def start(
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to gateway YAML.",
    ),
    port: int = typer.Option(
        int(os.environ.get("AGENT_PLATFORM_LLM_GATEWAY_PORT", "4000")),
        "--port",
        "-p",
        help="HTTP port to bind.",
    ),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind."),  # noqa: S104  # nosec B104 — LAN service; operators override --host
    num_workers: int = typer.Option(
        int(os.environ.get("AGENT_PLATFORM_LLM_GATEWAY_WORKERS", "2")),
        "--num-workers",
        min=1,
        help="LiteLLM worker process count.",
    ),
) -> None:
    """Validate config, then exec LiteLLM proxy.

    The validator runs *before* the exec so a misconfigured deploy
    fails in <1 second rather than after LiteLLM has half-initialised.
    """
    path = config or _default_config_path()
    try:
        cfg = load_config(path)
        validate_config(cfg)
    except FileNotFoundError:
        typer.echo(f"config not found: {path}", err=True)
        raise typer.Exit(2) from None
    except GatewayConfigError as exc:
        typer.echo(f"invalid config: {exc}", err=True)
        raise typer.Exit(1) from exc

    # C18 (harness H-11, #212): resolve env:// / vault:// secret references
    # before the exec — same fail-fast posture as the validator. ALL secret
    # values (resolved and literal) go into env vars (inherited across
    # execvp); the rewritten config carries only os.environ/ indirections,
    # so the 0600 temp file holds no secret material.
    try:
        resolved_cfg, changed = resolve_config_secrets(cfg)
        resolve_ingest_token_env()
    except GatewaySecretsError as exc:
        typer.echo(f"secrets bootstrap failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    if changed:
        fd, resolved_path = tempfile.mkstemp(prefix="agent-platform-llm-gateway-", suffix=".yaml")
        with os.fdopen(fd, "w") as fh:  # mkstemp is already 0600
            yaml.safe_dump(resolved_cfg, fh)
        path = Path(resolved_path)

    cmd = [
        "litellm",
        "--config",
        str(path),
        "--port",
        str(port),
        "--host",
        host,
        "--num_workers",
        str(num_workers),
    ]
    logger.info("starting LiteLLM proxy: %s", " ".join(cmd))
    # Default LiteLLM's own logs to JSON (harness H-12, #213) so the gateway
    # leg of a correlation-ID grep is structured like control's. Operators
    # can still opt out with an explicit JSON_LOGS=False.
    os.environ.setdefault("JSON_LOGS", "True")
    # Replace the wrapper with LiteLLM so systemd signals (ExecReload,
    # TimeoutStopSec, KillMode) target the real server process.
    os.execvp(cmd[0], cmd)  # noqa: S606  # execvp by design - systemd signals target LiteLLM  # nosec B606 — execvp by design for systemd signals
