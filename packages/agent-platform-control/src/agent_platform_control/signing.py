"""Offline image-version signature verification (PR-E, decision 12).

Verifies a base64-encoded RSA / ECDSA / Ed25519 signature against a
PEM-encoded public key + an arbitrary message (typically the OVA's hex
SHA-256 digest). Pure ``cryptography`` library — no cosign binary, no
sigstore-python online infra, no network calls. Works in air-gap.

Caching:
    Each verified ``(public_key, signature, message)`` triple is cached
    for the lifetime of the process so the clone-time path (decision 12
    E-2) doesn't re-verify on every clone. Set
    ``AGENT_PLATFORM_SIGNATURE_CACHE_DISABLED=1`` to disable when debugging
    false positives.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa


class SignatureVerificationError(ValueError):
    """Raised when a signature does not verify against the given key + message."""


# Cache keyed on (sha256 of key PEM, sha256 of signature, sha256 of message)
# so the keys stay small + uniform regardless of PEM whitespace differences.
_CACHE: set[tuple[str, str, str]] = set()


def clear_signature_cache() -> None:
    """Drop every cached verification result. Useful in tests + after a
    key rotation."""
    _CACHE.clear()


def _cache_disabled() -> bool:
    return os.environ.get("AGENT_PLATFORM_SIGNATURE_CACHE_DISABLED", "") not in {"", "0", "false"}


def _cache_key(pub: bytes, sig: bytes, msg: bytes) -> tuple[str, str, str]:
    return (
        hashlib.sha256(pub).hexdigest(),
        hashlib.sha256(sig).hexdigest(),
        hashlib.sha256(msg).hexdigest(),
    )


def verify_signature(
    *,
    public_key_pem: bytes,
    signature_b64: str,
    message: str,
) -> None:
    """Verify ``signature_b64`` over ``message`` with ``public_key_pem``.

    Raises ``SignatureVerificationError`` (with an educational hint) if
    anything is wrong; returns normally on success.

    Supports RSA (PKCS1v15 + SHA-256), ECDSA (P-256 + SHA-256), and
    Ed25519. Other algorithms fail with a clear error pointing at the
    supported set.
    """
    # Decode signature first — catches obvious mis-typed input early.
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except (base64.binascii.Error, ValueError) as exc:
        raise SignatureVerificationError(
            f"could not decode signature_b64 as base64: {exc}. "
            "Expected the cosign-style base64 of the raw signature bytes."
        ) from exc

    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise SignatureVerificationError(
            f"could not load public key PEM: {exc}. "
            "Expected a SubjectPublicKeyInfo PEM (RSA / ECDSA / Ed25519)."
        ) from exc

    msg = message.encode("utf-8")

    if not _cache_disabled():
        key = _cache_key(public_key_pem, sig, msg)
        if key in _CACHE:
            return

    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(sig, msg, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(sig, msg, ec.ECDSA(hashes.SHA256()))
        elif isinstance(public_key, ed25519.Ed25519PublicKey):
            public_key.verify(sig, msg)
        else:
            raise SignatureVerificationError(
                f"unsupported public key type {type(public_key).__name__}. "
                "Supported: RSA, ECDSA (P-256), Ed25519."
            )
    except InvalidSignature as exc:
        raise SignatureVerificationError(
            "invalid signature for the given message + public key. "
            "Either the OVA blob was modified, or the signature was "
            "produced with a different key than the one configured in "
            "AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM."
        ) from exc

    if not _cache_disabled():
        _CACHE.add(_cache_key(public_key_pem, sig, msg))
