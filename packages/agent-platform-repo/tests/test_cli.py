"""Unit tests for the agent-platform-repo CLI.

These tests exercise argument parsing, env-var handling, and the
``--help`` surface only — they never hit a real devpi-server. The full
integration test (start devpi, init index, upload wheel) lives in
``tests/test_integration.py`` (marked ``integration``) and runs only
when a devpi container is available, which CI does not currently
provide.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from agent_platform_repo import __version__
from agent_platform_repo.auth import (
    DEFAULT_INDEX,
    DEFAULT_URL,
    Credentials,
    MissingCredentialError,
    load_credentials,
)
from agent_platform_repo.cli import app
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# auth.load_credentials
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_credentials_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DEVPI_URL", "DEVPI_USER", "DEVPI_PASSWORD", "DEVPI_INDEX"):
        monkeypatch.delenv(var, raising=False)

    creds = load_credentials()

    assert creds == Credentials(
        url=DEFAULT_URL,
        user="root",
        password=None,
        index=DEFAULT_INDEX,
    )
    assert creds.index_url == f"{DEFAULT_URL}/{DEFAULT_INDEX}"
    assert creds.simple_url.endswith("/+simple/")


@pytest.mark.unit
def test_load_credentials_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVPI_URL", "https://devpi.example.com")
    monkeypatch.setenv("DEVPI_USER", "alice")
    monkeypatch.setenv("DEVPI_PASSWORD", "s3cret")
    monkeypatch.setenv("DEVPI_INDEX", "alice/dev")

    creds = load_credentials(require_password=True)

    assert creds.url == "https://devpi.example.com"
    assert creds.user == "alice"
    assert creds.password == "s3cret"
    assert creds.index == "alice/dev"
    assert creds.simple_url == "https://devpi.example.com/alice/dev/+simple/"


@pytest.mark.unit
def test_credentials_repr_never_leaks_password() -> None:
    creds = Credentials(
        url="https://devpi.example.com",
        user="alice",
        password="s3cret",  # test fixture, not a real credential
        index="alice/dev",
    )
    assert "s3cret" not in repr(creds)
    assert "s3cret" not in str(creds)


@pytest.mark.unit
def test_load_credentials_require_password_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVPI_PASSWORD", raising=False)

    with pytest.raises(MissingCredentialError):
        load_credentials(require_password=True)


@pytest.mark.unit
def test_load_credentials_empty_password_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVPI_PASSWORD", "")
    with pytest.raises(MissingCredentialError):
        load_credentials(require_password=True)


@pytest.mark.unit
def test_load_credentials_rejects_plaintext_http_remote_for_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SEC-10: a write op over plaintext http:// to a remote host would ship the
    # password in the clear — refuse it.
    monkeypatch.setenv("DEVPI_URL", "http://devpi.internal:3141")
    monkeypatch.setenv("DEVPI_PASSWORD", "s3cret")
    with pytest.raises(MissingCredentialError, match="plaintext http"):
        load_credentials(require_password=True)


@pytest.mark.unit
def test_load_credentials_allows_localhost_http_for_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # localhost http:// is fine for dev (no wire to sniff).
    monkeypatch.setenv("DEVPI_URL", "http://127.0.0.1:3141")
    monkeypatch.setenv("DEVPI_PASSWORD", "s3cret")
    creds = load_credentials(require_password=True)
    assert creds.url == "http://127.0.0.1:3141"


@pytest.mark.unit
def test_load_credentials_rejects_password_over_http_remote_even_for_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SEC-10 tightened: _client() attaches Basic Auth whenever a password is in
    # the env — read ops would leak it over plaintext http just the same, so
    # the combination is refused regardless of require_password.
    monkeypatch.setenv("DEVPI_URL", "http://devpi.internal:3141")
    monkeypatch.setenv("DEVPI_PASSWORD", "s3cret")
    with pytest.raises(MissingCredentialError, match="plaintext http"):
        load_credentials()


@pytest.mark.unit
def test_load_credentials_allows_ipv6_localhost_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # http://[::1]:3141 is localhost; the bracketed IPv6 literal must parse as
    # host '::1', not '[' (urlsplit().hostname strips the brackets).
    monkeypatch.setenv("DEVPI_URL", "http://[::1]:3141")
    monkeypatch.setenv("DEVPI_PASSWORD", "s3cret")
    creds = load_credentials(require_password=True)
    assert creds.url == "http://[::1]:3141"


# ---------------------------------------------------------------------------
# CLI surface (Typer CliRunner)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "upload", "verify", "list", "cleanup"):
        assert cmd in result.stdout


@pytest.mark.unit
def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["init", "upload", "verify", "list", "cleanup"])
def test_subcommand_help(subcommand: str) -> None:
    result = runner.invoke(app, [subcommand, "--help"])
    assert result.exit_code == 0
    assert subcommand in result.stdout.lower()


@pytest.mark.unit
def test_init_without_password_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVPI_PASSWORD", raising=False)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "DEVPI_PASSWORD" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_upload_rejects_unknown_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVPI_PASSWORD", "x")
    bogus = tmp_path / "not-a-wheel.txt"
    bogus.write_text("nope")

    result = runner.invoke(app, ["upload", str(bogus)])

    assert result.exit_code == 2
    assert "unsupported artifact type" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_upload_rejects_bare_gz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`foo.gz` is not a sdist — only `.tar.gz` qualifies. The old suffix-set
    check (`.gz` in {...}) let it through, contradicting the error message."""
    monkeypatch.setenv("DEVPI_PASSWORD", "x")
    bogus = tmp_path / "foo.gz"
    bogus.write_bytes(b"\x1f\x8b")

    result = runner.invoke(app, ["upload", str(bogus)])

    assert result.exit_code == 2
    assert "unsupported artifact type" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_upload_accepts_tar_zst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVPI_URL", raising=False)
    monkeypatch.setenv("DEVPI_PASSWORD", "x")
    bundle = tmp_path / "scale-v0.1.0.tar.zst"
    bundle.write_bytes(b"\x28\xb5\x2f\xfd")
    _patch_client(monkeypatch, lambda _req: httpx.Response(200, json={}))

    result = runner.invoke(app, ["upload", str(bundle)])

    assert result.exit_code == 0
    assert "uploaded" in result.stdout


@pytest.mark.unit
def test_upload_missing_password_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVPI_PASSWORD", raising=False)
    wheel = tmp_path / "example-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04")  # minimal "looks like a zip" payload

    result = runner.invoke(app, ["upload", str(wheel)])

    assert result.exit_code == 2
    assert "DEVPI_PASSWORD" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_verify_missing_signature_exits_2(tmp_path: Path) -> None:
    artifact = tmp_path / "scale-v0.1.0.tar.zst"
    artifact.write_bytes(b"\x28\xb5\x2f\xfd")  # zstd magic, content irrelevant

    result = runner.invoke(app, ["verify", str(artifact)])

    assert result.exit_code == 2
    assert "signature not found" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_verify_unimplemented_fails_closed_with_exit_5(tmp_path: Path) -> None:
    """Fail-closed: until C9 is wired in, verify must NOT exit 0 — a supply
    chain script branching on its exit code would treat the skeleton as a
    successful signature check."""
    artifact = tmp_path / "scale-v0.1.0.tar.zst"
    artifact.write_bytes(b"\x28\xb5\x2f\xfd")
    sig = tmp_path / "scale-v0.1.0.tar.zst.sig"
    sig.write_bytes(b"dummy-sig")

    result = runner.invoke(app, ["verify", str(artifact)])

    output = result.stdout + (result.stderr or "")
    assert result.exit_code == 5
    assert "not implemented" in output
    assert "agent-platform-bundle verify" in output


# ---------------------------------------------------------------------------
# list / cleanup subcommands (added in doc 30 PR-Buf-3)
# ---------------------------------------------------------------------------


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Make cli._client() return a MockTransport-backed httpx.Client so the
    list / cleanup commands do not touch the network.

    The Typer commands ignore the passed creds (we only mock the transport),
    so the unused ``creds`` / ``timeout`` parameters match the real ``_client``
    signature for monkeypatch compatibility.
    """

    def fake_client(creds: Credentials, *, timeout: float = 30.0) -> httpx.Client:
        del creds, timeout  # signature matches cli._client; values unused.
        return httpx.Client(
            base_url="http://devpi.example",
            transport=httpx.MockTransport(handler),
            headers={"Accept": "application/json"},
        )

    import agent_platform_repo.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_client", fake_client)


@pytest.mark.unit
def test_list_no_project_prints_project_names(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"alpha": "...", "beta": "..."}})

    _patch_client(monkeypatch, handler)
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


@pytest.mark.unit
def test_list_with_project_prints_versions_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/agent-runtime"):
            return httpx.Response(200, json={"result": {"1.10.0": {}, "1.2.0": {}}})
        return httpx.Response(404)

    _patch_client(monkeypatch, handler)
    result = runner.invoke(app, ["list", "agent-runtime"])

    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # Newest-first: 1.10.0 before 1.2.0.
    assert lines == ["1.10.0", "1.2.0"]


@pytest.mark.unit
def test_list_empty_index_prints_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda _: httpx.Response(200, json={"result": {}}))
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "(empty)" in result.stdout


@pytest.mark.unit
def test_list_devpi_4xx_exits_4_with_teaching_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, lambda _: httpx.Response(404, text="not found"))
    result = runner.invoke(app, ["list", "ghost"])
    assert result.exit_code == 4
    assert "unknown project" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_cleanup_dry_run_does_not_require_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEVPI_PASSWORD", raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path.endswith("/root/agent-platform/"):
            return httpx.Response(200, json={"result": {"alpha": "..."}})
        if req.method == "GET" and req.url.path.endswith("/alpha"):
            return httpx.Response(200, json={"result": {"2.0.0": {}, "1.0.0": {}}})
        return httpx.Response(404)

    _patch_client(monkeypatch, handler)
    result = runner.invoke(app, ["cleanup", "--retain", "1", "--dry-run"])

    assert result.exit_code == 0
    assert "dry-run" in result.stdout
    assert "alpha" in result.stdout
    assert "1.0.0" in result.stdout


@pytest.mark.unit
def test_cleanup_without_password_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVPI_PASSWORD", raising=False)
    result = runner.invoke(app, ["cleanup", "--retain", "1"])
    assert result.exit_code == 2
    assert "DEVPI_PASSWORD" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_cleanup_devpi_5xx_exits_4_with_teaching_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A devpi 5xx surfaces as RepoIndexError — the CLI must translate it to
    the existing 'devpi rejected' exit code 4 (same as list), not a traceback."""
    monkeypatch.delenv("DEVPI_URL", raising=False)
    monkeypatch.setenv("DEVPI_PASSWORD", "x")
    _patch_client(monkeypatch, lambda _req: httpx.Response(500, text="boom"))

    result = runner.invoke(app, ["cleanup", "--retain", "1"])

    assert result.exit_code == 4
    assert "error:" in (result.stdout + (result.stderr or ""))


@pytest.mark.unit
def test_list_with_password_and_http_remote_exits_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read command + env password + plaintext http remote → refuse before any
    request goes out (the client would attach Basic Auth to GETs too)."""
    monkeypatch.setenv("DEVPI_URL", "http://devpi.internal:3141")
    monkeypatch.setenv("DEVPI_PASSWORD", "s3cret")

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 2
    assert "plaintext http" in (result.stdout + (result.stderr or ""))
