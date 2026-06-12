"""Execute an AgentLaunchSpec and emit telemetry.

This is intentionally tiny — the heavy lifting is the Protocol + adapters.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 — dispatcher's purpose is launching curated agent binaries
import time
from dataclasses import dataclass

from .protocol import AgentBackend, UserContext
from .telemetry import NullShim, TelemetryEvent, TelemetryShim

# SEC-15: don't hand the WHOLE host environment to a third-party agent binary —
# it may hold other services' secrets. The agent's own config (gateway URL, API
# key, provider) arrives via the curated spec.env; from the host we pass only a
# minimal system allowlist needed to run + reach the network (PATH, HOME, locale,
# TLS trust, proxy).
_HOST_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TZ",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        # TLS trust store + proxy so the agent can reach its model endpoint.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


@dataclass(frozen=True)
class AgentRunResult:
    agent: str
    model: str | None
    returncode: int
    duration_s: float
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None = None


def run_agent(
    agent: AgentBackend,
    ctx: UserContext,
    prompt: str,
    *,
    shim: TelemetryShim | None = None,
) -> AgentRunResult:
    """Execute the agent; never raises on agent failure (caller-friendly).

    Returns AgentRunResult with rich error info; emits TelemetryEvent even on failure.
    """
    shim = shim or NullShim()
    spec = agent.launch_spec(ctx, prompt)

    cmd = (spec.binary, *spec.args)
    base_env = {k: v for k, v in os.environ.items() if k in _HOST_ENV_ALLOWLIST}
    merged_env = {**base_env, **spec.env}

    # Prompt delivery: when the adapter put the prompt in spec.stdin_data
    # (CLIs that read it from stdin), pipe it in as bytes — keeping it off
    # argv so ps/proc can't read it and a long prompt can't hit ARG_MAX.
    # Otherwise feed an explicit empty stdin (DEVNULL) so an agent that
    # blocks on a stdin read doesn't hang waiting on the inherited tty.
    stdin_bytes: bytes | None
    stdin_arg: int | None
    if spec.stdin_data is not None:
        stdin_bytes = spec.stdin_data.encode("utf-8")
        stdin_arg = None  # input= sets stdin=PIPE for us
    else:
        stdin_bytes = None
        stdin_arg = subprocess.DEVNULL

    t0 = time.perf_counter()
    timed_out = False
    err: str | None = None
    try:
        proc = subprocess.run(  # noqa: S603 — controlled  # nosec B603 — argv from curated AgentLaunchSpec, never shell
            cmd,
            env=merged_env,
            cwd=spec.cwd,
            input=stdin_bytes,
            stdin=stdin_arg,
            capture_output=True,
            timeout=spec.timeout_s,
            check=False,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = (e.stdout or b"").decode("utf-8", errors="replace")
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")
        rc = -9
        err = f"timeout after {spec.timeout_s}s"
    except FileNotFoundError:
        stdout, stderr, rc = "", "", -2
        err = (
            f"binary {spec.binary!r} not found — try `which {spec.binary}` "
            f"or see eval/agents/{agent.name}/install.md"
        )
    duration = time.perf_counter() - t0

    result = AgentRunResult(
        agent=agent.name,
        model=ctx.model or agent.default_model,
        returncode=rc,
        duration_s=round(duration, 3),
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        error=err,
    )

    shim.send(
        TelemetryEvent(
            user_id=ctx.user_id,
            agent=agent.name,
            model=result.model,
            tool=None,
            in_tokens=None,
            out_tokens=None,
            duration_s=result.duration_s,
            success=(rc == 0 and not timed_out),
            error=err,
        )
    )
    return result
