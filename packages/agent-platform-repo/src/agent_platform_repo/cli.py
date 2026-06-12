"""`agent-platform-repo` CLI — wraps the devpi-server lifecycle for Agent Platform.

This is the M1 demo skeleton. The CLI talks to a devpi-server instance
brought up via the sibling ``docker-compose.yml``; it does **not** embed
devpi itself. The three commands cover the minimal happy path for the
M1 acceptance demo (admin uploads a signed scale bundle wheel → C4
indexes it → tenants ``pip install`` from C4):

    init     : create the ``root/agent-platform`` index on a fresh devpi
    upload   : POST a wheel/sdist to the index (multipart, devpi protocol)
    verify   : placeholder for cosign signature check (TODO: wire C9)

The CLI deliberately stays thin — heavy lifting lives in :mod:`agent_platform_repo.auth`
and the future integration modules. Network errors surface as exit codes,
not stack traces, so operators get actionable output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import typer

from . import __version__
from .auth import Credentials, MissingCredentialError, load_credentials

app = typer.Typer(
    name="agent-platform-repo",
    help="Agent Platform C4 private PyPI (devpi) management CLI.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agent-platform-repo {__version__}")
        raise typer.Exit()


# Typer requires its option/argument objects to live in function defaults; B008
# (no calls in defaults) is the wrong heuristic for this framework — silence it
# locally for the three command signatures below.
_VERSION_OPT = typer.Option(
    None,
    "--version",
    callback=_version_callback,
    is_eager=True,
    help="Print version and exit.",
)
_TITLE_OPT = typer.Option(
    "Agent Platform private index", "--title", help="Human-readable index title."
)
_BASES_OPT = typer.Option(
    "root/pypi",
    "--bases",
    help="Comma-separated upstream indexes to inherit from "
    "(default: root/pypi — devpi's public PyPI mirror).",
)
_ARTIFACT_UPLOAD_ARG = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Wheel or sdist to upload (.whl / .tar.gz).",
)
_ARTIFACT_VERIFY_ARG = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Artifact whose detached signature should be verified.",
)
_SIG_OPT = typer.Option(None, "--sig", help="Signature path. Defaults to <artifact>.sig.")
_KEY_OPT = typer.Option(
    None,
    "--key",
    exists=False,
    help="Cosign public key. If omitted, looks up COSIGN_PUBLIC_KEY env var.",
)
_LIST_PROJECT_ARG = typer.Argument(
    None,
    help="If given, list versions of this project; otherwise list all projects.",
)
_CLEANUP_RETAIN_OPT = typer.Option(
    5,
    "--retain",
    min=0,
    help="Keep this many newest versions per project; delete older ones.",
)
_CLEANUP_DRY_RUN_OPT = typer.Option(
    False,
    "--dry-run",
    help="Print the deletion plan without sending DELETE.",
)


@app.callback()
def _root(version: bool | None = _VERSION_OPT) -> None:
    """Agent Platform C4 private PyPI (devpi) management CLI."""


def _client(creds: Credentials, *, timeout: float = 30.0) -> httpx.Client:
    """Build an httpx client carrying basic auth + devpi's JSON accept header.

    devpi returns either HTML or JSON depending on the ``Accept`` header;
    we always ask for JSON so output is machine-parseable.
    """
    auth = (creds.user, creds.password) if creds.password else None
    return httpx.Client(
        base_url=creds.url,
        auth=auth,
        timeout=timeout,
        headers={"Accept": "application/json"},
    )


@app.command("init")
def init_cmd(title: str = _TITLE_OPT, bases: str = _BASES_OPT) -> None:
    """Create the ``root/agent-platform`` index on a fresh devpi-server.

    Idempotent: if the index already exists, exits 0 with a notice.
    Requires ``DEVPI_PASSWORD`` (root password set by ``devpi-init``).
    """
    try:
        creds = load_credentials(require_password=True)
    except MissingCredentialError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from e

    payload = {"type": "stage", "bases": bases, "title": title}
    target = f"/{creds.index}"

    try:
        with _client(creds) as http:
            # PUT is the devpi protocol for index creation.
            resp = http.put(target, json=payload)
    except httpx.HTTPError as e:
        typer.echo(f"error: cannot reach devpi at {creds.url}: {e}", err=True)
        raise typer.Exit(code=3) from e

    if resp.status_code in (200, 201):
        typer.echo(f"created: {creds.index_url}")
        return
    if resp.status_code == 409:
        typer.echo(f"exists: {creds.index_url} (no-op)")
        return
    typer.echo(
        f"error: devpi rejected init (HTTP {resp.status_code}): {resp.text[:200]}",
        err=True,
    )
    raise typer.Exit(code=4)


@app.command("upload")
def upload_cmd(artifact: Path = _ARTIFACT_UPLOAD_ARG) -> None:
    """Upload a wheel/sdist to the configured devpi index.

    Wraps devpi's ``submit`` protocol (multipart POST with action=submit).
    Requires ``DEVPI_PASSWORD``.
    """
    # Full-name check so bare `.gz` / `.zst` files (not tarballs) are refused —
    # matches the error message below exactly.
    if not artifact.name.lower().endswith((".whl", ".tar.gz", ".tar.zst")):
        typer.echo(
            f"error: unsupported artifact type '{artifact.name}' "
            "(expected .whl, .tar.gz, or .tar.zst).",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        creds = load_credentials(require_password=True)
    except MissingCredentialError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from e

    target = f"/{creds.index}/"
    data = {":action": "file_upload"}
    try:
        with artifact.open("rb") as fh, _client(creds, timeout=120.0) as http:
            files = {"content": (artifact.name, fh, "application/octet-stream")}
            resp = http.post(target, data=data, files=files)
    except httpx.HTTPError as e:
        typer.echo(f"error: upload failed: {e}", err=True)
        raise typer.Exit(code=3) from e

    if resp.status_code in (200, 201):
        typer.echo(f"uploaded: {artifact.name} -> {creds.index_url}")
        return
    typer.echo(
        f"error: devpi rejected upload (HTTP {resp.status_code}): {resp.text[:200]}",
        err=True,
    )
    raise typer.Exit(code=4)


@app.command("verify")
def verify_cmd(
    artifact: Path = _ARTIFACT_VERIFY_ARG,
    sig: Path | None = _SIG_OPT,
    key: Path | None = _KEY_OPT,
) -> None:
    """Verify a scale-bundle signature before uploading. NOT IMPLEMENTED YET.

    Fail-closed skeleton: until the real check (delegating to
    ``agent-platform-scale-bundle`` / cosign, task 1.8 follow-up) is wired in,
    this command always exits non-zero so a supply-chain script can never
    mistake the placeholder for a passed signature check.

    Exit codes:
        2 — signature file not found
        5 — verification not implemented (always, today)
        4 — (future) cosign rejected the signature
    """
    sig_path = sig or artifact.with_suffix(artifact.suffix + ".sig")
    if not sig_path.exists():
        typer.echo(f"error: signature not found: {sig_path}", err=True)
        raise typer.Exit(code=2)

    # TODO(task 1.8 follow-up): import agent_platform_scale_bundle.cosign.verify
    # and call it here once C9 is on PyPI.
    key_repr = str(key) if key else "$COSIGN_PUBLIC_KEY (env)"
    typer.echo(
        f"error: verify is not implemented yet — refusing to report success.\n"
        f"  inputs seen: artifact={artifact.name} sig={sig_path.name} key={key_repr}\n"
        f"  Do not gate anything on this command's exit code; run "
        f"`agent-platform-bundle verify` (agent-platform-scale-bundle, C9) for a "
        f"real cosign signature check until task 1.8 wires it in here.",
        err=True,
    )
    raise typer.Exit(code=5)


@app.command("list")
def list_cmd(project: str | None = _LIST_PROJECT_ARG) -> None:
    """List projects on the index, or versions of one project.

    Read-only: does not require ``DEVPI_PASSWORD``. Useful for the M2
    installer (C8) to ask "what versions of agent-platform-runtime can I
    install" without first learning the devpi protocol.
    """
    from .index import RepoIndexError, list_projects, list_versions

    try:
        creds = load_credentials()
    except MissingCredentialError as e:
        # Reads don't need a password, but a password + insecure URL combo is
        # refused by load_credentials (SEC-10) and must exit cleanly.
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from e
    try:
        with _client(creds) as http:
            if project is None:
                names = list_projects(http, index=creds.index)
                if not names:
                    typer.echo(f"(empty) {creds.index_url}")
                    return
                for name in names:
                    typer.echo(name)
                return
            versions = list_versions(http, project, index=creds.index)
            if not versions:
                typer.echo(f"(no releases) {creds.index_url}/{project}")
                return
            for v in versions:
                typer.echo(v)
    except RepoIndexError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=4) from e
    except httpx.HTTPError as e:
        typer.echo(f"error: cannot reach devpi at {creds.url}: {e}", err=True)
        raise typer.Exit(code=3) from e


@app.command("cleanup")
def cleanup_cmd(
    retain: int = _CLEANUP_RETAIN_OPT,
    dry_run: bool = _CLEANUP_DRY_RUN_OPT,
) -> None:
    """Retain the N newest versions per project; delete the rest.

    Default keeps 5 newest per project. Pass ``--dry-run`` first to
    preview the plan. Stops on the first DELETE failure so an operator
    can investigate before continuing.

    Exit codes:
        0 — cleanup completed (possibly with 0 deletions)
        2 — missing credentials (write op)
        3 — devpi unreachable
        4 — devpi rejected (listing failed, or mid-walk failure; partial state)
    """
    from .cleanup import cleanup as run_cleanup
    from .index import RepoIndexError

    try:
        creds = load_credentials(require_password=not dry_run)
    except MissingCredentialError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from e

    try:
        with _client(creds) as http:
            plans, report = run_cleanup(http, index=creds.index, retain=retain, dry_run=dry_run)
    except RepoIndexError as e:
        # devpi answered but with a non-2xx / malformed body (e.g. 5xx while
        # listing) — same "devpi rejected" exit code as the list command.
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=4) from e
    except httpx.HTTPError as e:
        typer.echo(f"error: cannot reach devpi at {creds.url}: {e}", err=True)
        raise typer.Exit(code=3) from e

    for plan in plans:
        if not plan.drop:
            continue
        typer.echo(
            f"{plan.project}: keep {len(plan.keep)}, drop {len(plan.drop)} ({', '.join(plan.drop)})"
        )
    if dry_run:
        typer.echo(f"dry-run: nothing deleted (retain={retain})")
        return

    typer.echo(f"deleted {len(report.deleted)} version(s)")
    if report.failed:
        for key, detail in report.failed.items():
            typer.echo(f"failed: {key} -- {detail}", err=True)
        raise typer.Exit(code=4)


def main() -> None:  # pragma: no cover - tiny shim used by `python -m agent_platform_repo`
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
