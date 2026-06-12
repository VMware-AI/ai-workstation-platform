"""`agent-platform-bundle` CLI: pack / sign / verify.

Designed to be re-exported by ``agent-platform-installer`` as the ``verify`` subcommand
(task 1.9.3) — the verify path here is the single source of truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .bundle import BundleError, pack, sha256_file
from .cosign import CosignError, sign, verify


@click.group(help="Agent Platform scale bundle: pack / sign / verify a release artifact.")
@click.version_option(__version__, prog_name="agent-platform-bundle")
def main() -> None:
    pass


@main.command("pack")
@click.argument("src", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("out", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--level", default=10, show_default=True, help="zstd compression level 1-22")
def pack_cmd(src: Path, out: Path, level: int) -> None:
    """Pack SRC directory into OUT (must end with .tar.zst)."""
    try:
        r = pack(src, out, level=level)
    except BundleError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    click.echo(f"packed: {r.artifact}  ({r.size_bytes:,} bytes)")
    click.echo(f"sha256: {r.sha256}")


@main.command("sha256")
@click.argument("artifact", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def sha256_cmd(artifact: Path) -> None:
    """Print SHA-256 of ARTIFACT."""
    click.echo(sha256_file(artifact))


@main.command("sign")
@click.argument("artifact", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--key", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--sig",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Signature output path. Defaults to <artifact>.sig.",
)
@click.option("--cosign-bin", default=None, help="Path to cosign binary (defaults to PATH).")
def sign_cmd(
    artifact: Path,
    key: Path,
    sig: Path | None,
    cosign_bin: str | None,
) -> None:
    """Sign ARTIFACT with cosign key (writes <artifact>.sig).

    The key password is read from the COSIGN_PASSWORD environment variable
    (export COSIGN_PASSWORD="" for unencrypted keys). The former --password
    flag was removed: flag values leak into shell history and `ps` output.
    """
    sig_path = sig or artifact.with_suffix(artifact.suffix + ".sig")
    try:
        # password=None → cosign reads COSIGN_PASSWORD from the inherited env.
        out = sign(artifact, key, sig_path, cosign_bin=cosign_bin, password=None)
    except CosignError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    click.echo(f"signed: {out}")


@main.command("verify")
@click.argument("artifact", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--key", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--sig",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Signature path. Defaults to <artifact>.sig.",
)
@click.option("--cosign-bin", default=None, help="Path to cosign binary (defaults to PATH).")
def verify_cmd(
    artifact: Path,
    key: Path,
    sig: Path | None,
    cosign_bin: str | None,
) -> None:
    """Verify ARTIFACT against signature. Exit 0 = ok, 4 = mismatch."""
    sig_path = sig or artifact.with_suffix(artifact.suffix + ".sig")
    try:
        verify(artifact, key, sig_path, cosign_bin=cosign_bin)
    except CosignError as e:
        click.echo(f"VERIFY FAILED: {e}", err=True)
        sys.exit(4)
    click.echo(f"ok: {artifact.name} verified against {sig_path.name}")


if __name__ == "__main__":  # pragma: no cover
    main()
