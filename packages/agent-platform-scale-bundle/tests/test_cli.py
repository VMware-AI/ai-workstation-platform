from __future__ import annotations

import stat
from pathlib import Path

import pytest
from agent_platform_scale_bundle.cli import main
from click.testing import CliRunner


def _fake_cosign(tmp_path: Path, *, exit_code: int) -> Path:
    fake = tmp_path / "fake-cosign"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if '--bundle' in args and 'sign-blob' in args:\n"
        "    i = args.index('--bundle')\n"
        "    open(args[i+1], 'w').write('bundle\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


@pytest.mark.unit
def test_cli_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "agent-platform-bundle" in result.output


@pytest.mark.unit
def test_cli_sha256(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    result = CliRunner().invoke(main, ["sha256", str(f)])
    assert result.exit_code == 0
    assert (
        result.output.strip() == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


@pytest.mark.unit
def test_cli_sign(tmp_path: Path) -> None:
    artifact = tmp_path / "b.tar.zst"
    artifact.write_bytes(b"x")
    key = tmp_path / "k.key"
    key.write_text("k")
    fake = _fake_cosign(tmp_path, exit_code=0)
    result = CliRunner().invoke(
        main,
        ["sign", str(artifact), "--key", str(key), "--cosign-bin", str(fake)],
        env={"COSIGN_PASSWORD": ""},
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "b.tar.zst.sig").is_file()


@pytest.mark.unit
def test_cli_sign_password_flag_removed(tmp_path: Path) -> None:
    """SEC: --password leaked the key password into shell history / ps output;
    the flag is gone — COSIGN_PASSWORD env var is the only mechanism."""
    artifact = tmp_path / "b.tar.zst"
    artifact.write_bytes(b"x")
    key = tmp_path / "k.key"
    key.write_text("k")
    result = CliRunner().invoke(
        main,
        ["sign", str(artifact), "--key", str(key), "--password", ""],
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


@pytest.mark.unit
def test_cli_sign_help_points_to_env_var() -> None:
    result = CliRunner().invoke(main, ["sign", "--help"])
    assert result.exit_code == 0
    assert "COSIGN_PASSWORD" in result.output


@pytest.mark.unit
def test_cli_verify_ok(tmp_path: Path) -> None:
    artifact = tmp_path / "b.tar.zst"
    artifact.write_bytes(b"x")
    key = tmp_path / "k.pub"
    key.write_text("k")
    sig = tmp_path / "b.tar.zst.sig"
    sig.write_text("sig")
    fake = _fake_cosign(tmp_path, exit_code=0)
    result = CliRunner().invoke(
        main, ["verify", str(artifact), "--key", str(key), "--cosign-bin", str(fake)]
    )
    assert result.exit_code == 0, result.output
    assert "ok:" in result.output


@pytest.mark.unit
def test_cli_verify_fails_with_exit_4(tmp_path: Path) -> None:
    """Critical contract: tamper → exit 4 so installer scripts can branch on it."""
    artifact = tmp_path / "b.tar.zst"
    artifact.write_bytes(b"x")
    key = tmp_path / "k.pub"
    key.write_text("k")
    sig = tmp_path / "b.tar.zst.sig"
    sig.write_text("sig")
    fake = _fake_cosign(tmp_path, exit_code=1)
    result = CliRunner().invoke(
        main, ["verify", str(artifact), "--key", str(key), "--cosign-bin", str(fake)]
    )
    assert result.exit_code == 4
    assert "VERIFY FAILED" in result.output
