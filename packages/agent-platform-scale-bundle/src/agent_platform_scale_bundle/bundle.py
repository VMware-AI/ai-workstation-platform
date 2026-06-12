"""Pack a source tree into a deterministic tar.zst artifact + sha256 sidecar."""

from __future__ import annotations

import hashlib
import tarfile
from dataclasses import dataclass
from pathlib import Path

# zstandard is optional at install time: bundle code can hash/verify without it,
# only `pack()` requires it.
try:
    import zstandard as _zstd
except ImportError:  # pragma: no cover - exercised only when extra missing
    _zstd = None


class BundleError(RuntimeError):
    """Raised when pack/unpack fails."""


@dataclass(frozen=True)
class PackResult:
    artifact: Path
    sha256: str
    size_bytes: int


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    """Stream a file through SHA-256 and return the hex digest."""
    if not path.is_file():
        raise BundleError(f"sha256: not a file: {path}")
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def pack(src: Path, out: Path, *, level: int = 10) -> PackResult:
    """Pack ``src`` (a directory) into ``out`` (a ``*.tar.zst`` path).

    Writes ``<out>.sha256`` next to the artifact. Returns a ``PackResult``.

    Determinism: entries are sorted by relative path and mtimes are zeroed so
    repeated packs of identical input produce identical bytes — that is what
    makes the sha256 a useful sanity check for the user before signing.
    """
    if _zstd is None:
        raise BundleError(
            "zstandard is required for pack(): "
            "install agent-platform-scale-bundle[pack] or `pip install zstandard`"
        )
    if not src.is_dir():
        raise BundleError(f"pack: source is not a directory: {src}")
    if out.suffix != ".zst" or not out.name.endswith(".tar.zst"):
        raise BundleError(f"pack: output must end in '.tar.zst', got: {out.name}")

    out.parent.mkdir(parents=True, exist_ok=True)
    cctx = _zstd.ZstdCompressor(level=level)
    entries = sorted(p for p in src.rglob("*") if p.is_file() or p.is_dir())

    with (
        out.open("wb") as raw,
        cctx.stream_writer(raw) as zwriter,
        tarfile.open(fileobj=zwriter, mode="w|") as tar,
    ):
        for path in entries:
            arcname = path.relative_to(src).as_posix()
            info = tar.gettarinfo(str(path), arcname=arcname)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            if info.isfile():
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
            else:
                tar.addfile(info)

    digest = sha256_file(out)
    sha_sidecar = out.with_suffix(out.suffix + ".sha256")
    sha_sidecar.write_text(f"{digest}  {out.name}\n")
    return PackResult(artifact=out, sha256=digest, size_bytes=out.stat().st_size)
