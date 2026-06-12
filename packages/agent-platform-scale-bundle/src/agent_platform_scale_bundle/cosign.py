"""Thin wrapper around the ``cosign`` binary for ``sign-blob`` / ``verify-blob``.

Why subprocess and not a Python crypto library: cosign is the canonical impl
(Sigstore) and the same binary is what runs in CI and on the customer side. A
pure-Python signer would diverge in subtle ways and confuse audits.

We keep the surface tiny on purpose — two functions, three exceptions. The
caller is expected to manage keys and paths.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — cosign CLI is the product
from pathlib import Path

# Hard ceiling for one cosign invocation. sign-blob / verify-blob are local
# operations (we disable all tlog network traffic), so two minutes is generous;
# without a timeout a half-dead network mount or stuck binary hangs forever.
DEFAULT_TIMEOUT_SECONDS = 120


class CosignError(RuntimeError):
    """Base for all cosign wrapper failures."""


class CosignNotFoundError(CosignError):
    """``cosign`` binary not on PATH and not given explicitly."""


class SignatureMismatchError(CosignError):
    """``cosign verify-blob`` returned non-zero (tamper / wrong key / bad sig)."""


def _resolve_cosign(cosign_bin: str | None) -> str:
    if cosign_bin:
        if not Path(cosign_bin).is_file():
            raise CosignNotFoundError(
                f"cosign binary not found at: {cosign_bin}. "
                f"Install: https://docs.sigstore.dev/system_config/installation/"
            )
        return cosign_bin
    found = shutil.which("cosign")
    if not found:
        raise CosignNotFoundError(
            "cosign binary not found on PATH. "
            "Install via `brew install cosign` (macOS) or release tarball "
            "(https://github.com/sigstore/cosign/releases), or pass cosign_bin=..."
        )
    return found


def _tlog_opt_out_flags(bin_path: str, *, timeout: float) -> list[str]:
    """Argv flags that keep ``sign-blob`` fully offline (no Rekor upload).

    cosign 2.x: ``--tlog-upload=false`` alone is enough.
    cosign 3.x: ``--use-signing-config`` defaults to true and REJECTS
    ``--tlog-upload=false`` unless signing-config mode is switched off first.

    Feature-detected from ``sign-blob --help`` rather than version-parsed; on
    any probe failure we fall back to the 2.x flag set (fail-open here is fine:
    the worst case is the original loud sign-blob error, never a silent upload).
    """
    flags = ["--tlog-upload=false"]
    try:
        probe = subprocess.run(  # noqa: S603 - bin_path validated by caller  # nosec B603
            [bin_path, "sign-blob", "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return flags
    if "--use-signing-config" in probe.stdout + probe.stderr:
        return ["--use-signing-config=false", *flags]
    return flags


def sign(
    artifact: Path,
    key: Path,
    sig_out: Path,
    *,
    cosign_bin: str | None = None,
    password: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """Sign ``artifact`` with cosign private ``key``; write signature to ``sig_out``.

    Returns ``sig_out`` on success. Raises ``CosignError`` on any failure,
    including the subprocess exceeding ``timeout`` seconds.

    ``password`` is passed via env var ``COSIGN_PASSWORD`` (the only mechanism
    cosign supports for non-interactive runs). Pass ``""`` for unencrypted keys.
    """
    if not artifact.is_file():
        raise CosignError(f"sign: artifact missing: {artifact}")
    if not key.is_file():
        raise CosignError(f"sign: key missing: {key}")
    sig_out.parent.mkdir(parents=True, exist_ok=True)

    bin_path = _resolve_cosign(cosign_bin)
    env = os.environ.copy()
    if password is not None:
        env["COSIGN_PASSWORD"] = password
    # cosign 2.6+ requires --bundle for sign-blob; the bundle is a JSON file that
    # carries the signature plus verification metadata. ``sig_out`` is the bundle
    # path — convention is ``<artifact>.sig`` so installer scripts find it.
    cmd = [
        bin_path,
        "sign-blob",
        "--yes",  # skip the tlog upload confirm prompt
        # SEC-9 counterpart on the sign side: cosign sign-blob defaults to
        # uploading the artifact sha256 + signing metadata to the PUBLIC Rekor
        # tlog — leaking internal artifact fingerprints and hanging air-gapped
        # builds. verify() ignores the tlog anyway (--insecure-ignore-tlog=true
        # there), so uploading buys nothing. Both sides flip together pre-GA
        # when Rekor / the M2 keyless OIDC flow lands.
        *_tlog_opt_out_flags(bin_path, timeout=timeout),
        "--key",
        str(key),
        "--bundle",
        str(sig_out),
        str(artifact),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - bin_path validated above, args are paths  # nosec B603 — constant argv to cosign
            cmd,
            env=env,
            # Closed stdin: if the key is encrypted and no COSIGN_PASSWORD was
            # supplied, cosign would otherwise prompt on the tty and block the
            # full timeout in non-interactive runs (CI). DEVNULL makes it fail
            # fast with cosign's own "password required" error instead.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CosignError(
            f"cosign sign-blob timed out after {timeout}s for {artifact.name}. "
            f"Signing is local — a hang usually means a dead network mount for "
            f"the key/artifact path or a stuck cosign binary; check both, then "
            f"retry (raise timeout= if the artifact is unusually large)."
        ) from exc
    if result.returncode != 0:
        raise CosignError(
            f"cosign sign-blob failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    if not sig_out.is_file():
        raise CosignError(f"cosign reported success but signature file missing: {sig_out}")
    return sig_out


def verify(
    artifact: Path,
    key: Path,
    sig: Path,
    *,
    cosign_bin: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Verify ``artifact`` against ``sig`` using public ``key``.

    Returns ``True`` on success. Raises ``SignatureMismatchError`` on any
    verification failure and ``CosignError`` if the subprocess exceeds
    ``timeout`` seconds — callers should treat any exception as "do not unpack".
    """
    for name, path in (("artifact", artifact), ("key", key), ("signature", sig)):
        if not path.is_file():
            raise SignatureMismatchError(f"verify: {name} missing: {path}")

    bin_path = _resolve_cosign(cosign_bin)
    cmd = [
        bin_path,
        "verify-blob",
        "--key",
        str(key),
        "--bundle",
        str(sig),
        # SEC-9 / RELEASE-BLOCKER: M1 uses keyed signing without Rekor uploads,
        # so transparency-log verification is disabled here. A leaked signing key
        # then can't be detected/revoked via the tlog and signing time is
        # unverifiable. This MUST be flipped to false (with Rekor, or the planned
        # M2 keyless OIDC flow) before any external/GA release — tracked as a
        # release blocker, not a silent default.
        "--insecure-ignore-tlog=true",
        str(artifact),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - bin_path validated above, args are paths  # nosec B603 — constant argv to cosign
            cmd,
            # Closed stdin so verify-blob can never block on an interactive
            # prompt in a non-tty run (it would otherwise hang the full
            # timeout) — verify must fail fast, not appear to "hang".
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CosignError(
            f"cosign verify-blob timed out after {timeout}s for {artifact.name}. "
            f"Verification is local (tlog disabled) — a hang usually means a "
            f"dead network mount for the artifact/sig path or a stuck cosign "
            f"binary. Treat as NOT verified; do not unpack."
        ) from exc
    if result.returncode != 0:
        raise SignatureMismatchError(
            f"cosign verify-blob FAILED for {artifact.name}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return True
