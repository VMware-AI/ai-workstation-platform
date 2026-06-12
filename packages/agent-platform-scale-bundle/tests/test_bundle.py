from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from agent_platform_scale_bundle.bundle import BundleError, pack, sha256_file

# No importorskip here on purpose: zstandard is part of the dev extra, so a
# missing module must FAIL the suite loudly, not silently skip the pack tests.


@pytest.mark.unit
def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    payload = b"agent-platform" * 1000
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


@pytest.mark.unit
def test_sha256_file_rejects_dir(tmp_path: Path) -> None:
    with pytest.raises(BundleError):
        sha256_file(tmp_path)


@pytest.mark.unit
def test_pack_produces_artifact_and_sidecar(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("hello")
    (src / "sub" / "b.txt").write_text("world")
    out = tmp_path / "bundle.tar.zst"

    result = pack(src, out)

    assert result.artifact == out
    assert out.is_file()
    sidecar = out.with_suffix(out.suffix + ".sha256")
    assert sidecar.is_file()
    assert result.sha256 in sidecar.read_text()


@pytest.mark.unit
def test_pack_is_deterministic(tmp_path: Path) -> None:
    """Same input → identical bytes; this is what makes sha256 actionable."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "x").write_text("payload")
    out1 = tmp_path / "a.tar.zst"
    out2 = tmp_path / "b.tar.zst"

    pack(src, out1)
    pack(src, out2)

    assert sha256_file(out1) == sha256_file(out2)


@pytest.mark.unit
def test_pack_rejects_non_zst_suffix(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "x").write_text("x")
    with pytest.raises(BundleError):
        pack(src, tmp_path / "bundle.tar.gz")


@pytest.mark.unit
def test_pack_rejects_missing_src(tmp_path: Path) -> None:
    with pytest.raises(BundleError):
        pack(tmp_path / "nope", tmp_path / "x.tar.zst")
