"""Cosign wrapper tests.

The default suite uses a fake cosign script so it runs anywhere (no real
cosign binary). The integration test at the end runs end-to-end with a real
cosign install if one is on PATH — it's the contract test that catches
behavior drift after cosign upgrades.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from agent_platform_scale_bundle.cosign import (
    CosignError,
    CosignNotFoundError,
    SignatureMismatchError,
    sign,
    verify,
)


def _make_fake_cosign(
    tmp_path: Path, *, exit_code: int, stderr: str = "", argv_log: Path | None = None
) -> Path:
    """Write a fake cosign binary that exits with a chosen code and optionally
    writes the signature file when invoked with --output-signature. When
    ``argv_log`` is given, the received argv is dumped there (one arg per line)
    so tests can assert the exact flags we pass to cosign."""
    fake = tmp_path / "fake-cosign"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, os\n"
        f"stderr = {stderr!r}\n"
        "if stderr:\n"
        "    sys.stderr.write(stderr)\n"
        "args = sys.argv[1:]\n"
        f"argv_log = {str(argv_log) if argv_log else ''!r}\n"
        "if argv_log:\n"
        "    open(argv_log, 'w').write('\\n'.join(args))\n"
        "if '--bundle' in args and 'sign-blob' in args:\n"
        "    i = args.index('--bundle')\n"
        "    open(args[i+1], 'w').write('fake-bundle\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


@pytest.fixture
def artifact_and_key(tmp_path: Path) -> tuple[Path, Path, Path]:
    artifact = tmp_path / "bundle.tar.zst"
    artifact.write_bytes(b"fake bundle content")
    key = tmp_path / "cosign.key"
    key.write_text("-----BEGIN COSIGN PRIVATE KEY-----\nfake\n")
    sig = tmp_path / "bundle.tar.zst.sig"
    return artifact, key, sig


@pytest.mark.unit
def test_sign_success(artifact_and_key: tuple[Path, Path, Path], tmp_path: Path) -> None:
    artifact, key, sig = artifact_and_key
    fake = _make_fake_cosign(tmp_path, exit_code=0)
    out = sign(artifact, key, sig, cosign_bin=str(fake), password="")
    assert out == sig
    assert sig.read_text() == "fake-bundle\n"


@pytest.mark.unit
def test_sign_propagates_cosign_failure(
    artifact_and_key: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    artifact, key, sig = artifact_and_key
    fake = _make_fake_cosign(tmp_path, exit_code=1, stderr="bad password")
    with pytest.raises(CosignError, match="bad password"):
        sign(artifact, key, sig, cosign_bin=str(fake), password="")


@pytest.mark.unit
def test_sign_argv_disables_tlog_upload(
    artifact_and_key: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """SEC: sign-blob must NOT upload artifact metadata to the public Rekor
    tlog (cosign 2.x default) — verify ignores the tlog anyway (SEC-9)."""
    artifact, key, sig = artifact_and_key
    argv_log = tmp_path / "argv.log"
    fake = _make_fake_cosign(tmp_path, exit_code=0, argv_log=argv_log)
    sign(artifact, key, sig, cosign_bin=str(fake), password="")
    args = argv_log.read_text().splitlines()
    assert "--tlog-upload=false" in args


@pytest.mark.unit
def test_sign_argv_disables_signing_config_on_cosign_3x(
    artifact_and_key: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """cosign 3.x rejects --tlog-upload=false while --use-signing-config
    (default true) is active — the wrapper must switch signing-config off
    when the binary advertises it."""
    artifact, key, sig = artifact_and_key
    argv_log = tmp_path / "argv.log"
    fake = tmp_path / "fake-cosign-3x"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if '--help' in args:\n"
        "    print('--use-signing-config=true: ...')\n"
        "    sys.exit(0)\n"
        f"open({str(argv_log)!r}, 'w').write('\\n'.join(args))\n"
        "if '--bundle' in args:\n"
        "    i = args.index('--bundle')\n"
        "    open(args[i+1], 'w').write('fake-bundle\\n')\n"
        "sys.exit(0)\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    sign(artifact, key, sig, cosign_bin=str(fake), password="")
    args = argv_log.read_text().splitlines()
    assert "--use-signing-config=false" in args
    assert "--tlog-upload=false" in args
    assert args.index("--use-signing-config=false") < args.index("--tlog-upload=false")


@pytest.mark.unit
def test_sign_timeout_raises_teaching_error(
    artifact_and_key: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, key, sig = artifact_and_key

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") == 120  # default DEFAULT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

    monkeypatch.setattr("agent_platform_scale_bundle.cosign.subprocess.run", fake_run)
    with pytest.raises(CosignError, match="timed out"):
        sign(artifact, key, sig, cosign_bin="/usr/bin/true", password="")


@pytest.mark.unit
def test_verify_timeout_raises_teaching_error(
    artifact_and_key: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, key, sig = artifact_and_key
    sig.write_text("sig")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") == 5  # caller-supplied override
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    monkeypatch.setattr("agent_platform_scale_bundle.cosign.subprocess.run", fake_run)
    with pytest.raises(CosignError, match="timed out"):
        verify(artifact, key, sig, cosign_bin="/usr/bin/true", timeout=5)


@pytest.mark.unit
def test_sign_closes_child_stdin(
    artifact_and_key: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No COSIGN_PASSWORD + an encrypted key makes cosign prompt on the tty;
    in CI (no tty) it would block until timeout. Passing stdin=DEVNULL makes
    cosign fail FAST instead of hanging 120s with a misleading 'timed out'."""
    artifact, key, sig = artifact_and_key
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["stdin"] = kwargs.get("stdin", "MISSING")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Real cosign would write the bundle; fake it so sign()'s post-check passes.
    monkeypatch.setattr("agent_platform_scale_bundle.cosign.subprocess.run", fake_run)
    sig.write_text("bundle")
    sign(artifact, key, sig, cosign_bin="/usr/bin/true", password="")
    assert captured["stdin"] == subprocess.DEVNULL


@pytest.mark.unit
def test_verify_closes_child_stdin(
    artifact_and_key: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, key, sig = artifact_and_key
    sig.write_text("sig")
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["stdin"] = kwargs.get("stdin", "MISSING")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_platform_scale_bundle.cosign.subprocess.run", fake_run)
    verify(artifact, key, sig, cosign_bin="/usr/bin/true")
    assert captured["stdin"] == subprocess.DEVNULL


@pytest.mark.unit
def test_sign_rejects_missing_artifact(tmp_path: Path) -> None:
    key = tmp_path / "k"
    key.write_text("k")
    with pytest.raises(CosignError, match="artifact missing"):
        sign(tmp_path / "nope", key, tmp_path / "s.sig", cosign_bin="/usr/bin/true")


@pytest.mark.unit
def test_verify_success(artifact_and_key: tuple[Path, Path, Path], tmp_path: Path) -> None:
    artifact, key, sig = artifact_and_key
    sig.write_text("sig")
    fake = _make_fake_cosign(tmp_path, exit_code=0)
    assert verify(artifact, key, sig, cosign_bin=str(fake)) is True


@pytest.mark.unit
def test_verify_failure_raises(artifact_and_key: tuple[Path, Path, Path], tmp_path: Path) -> None:
    """The canonical 'tamper detected' path — verify must raise, not return False."""
    artifact, key, sig = artifact_and_key
    sig.write_text("sig")
    fake = _make_fake_cosign(tmp_path, exit_code=1, stderr="signature mismatch")
    with pytest.raises(SignatureMismatchError, match="signature mismatch"):
        verify(artifact, key, sig, cosign_bin=str(fake))


@pytest.mark.unit
def test_verify_missing_sig_raises(
    artifact_and_key: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    artifact, key, _ = artifact_and_key
    with pytest.raises(SignatureMismatchError, match="signature missing"):
        verify(artifact, key, tmp_path / "nope.sig", cosign_bin="/usr/bin/true")


@pytest.mark.unit
def test_resolve_cosign_not_found(
    artifact_and_key: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, key, sig = artifact_and_key
    sig.write_text("sig")
    # Wipe PATH so shutil.which finds nothing.
    monkeypatch.setenv("PATH", "")
    with pytest.raises(CosignNotFoundError):
        verify(artifact, key, sig)


@pytest.mark.unit
def test_resolve_cosign_explicit_missing(
    artifact_and_key: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    artifact, key, sig = artifact_and_key
    sig.write_text("sig")
    with pytest.raises(CosignNotFoundError, match="cosign binary not found at"):
        verify(artifact, key, sig, cosign_bin=str(tmp_path / "missing-cosign"))


# ----------------------------------------------------------------------------
# Integration: only runs if a real cosign is on PATH. This is the eval that
# catches behavior drift when cosign upgrades — see docs/research/scale-bundle-security.md.
# ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("cosign") is None, reason="cosign not installed")
def test_tamper_detection_end_to_end(tmp_path: Path) -> None:
    """sign → verify ok → flip 1 byte → verify FAILS. The whole point of C9."""
    artifact = tmp_path / "bundle.tar.zst"
    artifact.write_bytes(b"original payload " * 100)

    priv = tmp_path / "cosign.key"
    pub = tmp_path / "cosign.pub"
    env = os.environ | {"COSIGN_PASSWORD": ""}
    subprocess.run(
        ["cosign", "generate-key-pair", f"--output-key-prefix={tmp_path}/cosign"],
        env=env,
        check=True,
        cwd=tmp_path,
    )
    assert priv.is_file() and pub.is_file()

    sig = tmp_path / "bundle.tar.zst.sig"
    sign(artifact, priv, sig, password="")
    assert verify(artifact, pub, sig) is True

    # Flip exactly 1 byte → must fail.
    raw = bytearray(artifact.read_bytes())
    raw[0] ^= 0x01
    artifact.write_bytes(bytes(raw))
    with pytest.raises(SignatureMismatchError):
        verify(artifact, pub, sig)
