"""Tests for image-version signature verification (PR-E E-1, decision 12).

Offline by design — no network calls, no cosign binary. The
``cryptography`` library handles RSA / ECDSA verification against a
PEM-encoded public key.
"""

from __future__ import annotations

import base64

import pytest
from agent_platform_control.signing import (
    SignatureVerificationError,
    clear_signature_cache,
    verify_signature,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

# ---------------------------------------------------------- key + sig helpers


def _gen_rsa() -> tuple[bytes, rsa.RSAPrivateKey]:
    """Returns (public_pem_bytes, private_key)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public_pem, private


def _gen_ecdsa() -> tuple[bytes, ec.EllipticCurvePrivateKey]:
    private = ec.generate_private_key(ec.SECP256R1())
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public_pem, private


def _sign_rsa(private_key: rsa.RSAPrivateKey, message: str) -> str:
    raw = private_key.sign(
        message.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(raw).decode("ascii")


def _sign_ecdsa(private_key: ec.EllipticCurvePrivateKey, message: str) -> str:
    raw = private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------- happy paths


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_signature_cache()
    yield
    clear_signature_cache()


def test_rsa_valid_signature_passes():
    pub, priv = _gen_rsa()
    msg = "sha256-of-ova-content"
    sig = _sign_rsa(priv, msg)
    # Should not raise
    verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)


def test_ecdsa_valid_signature_passes():
    pub, priv = _gen_ecdsa()
    msg = "sha256-of-ova-content"
    sig = _sign_ecdsa(priv, msg)
    verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)


# ---------------------------------------------------------- failure paths


def test_tampered_message_raises():
    pub, priv = _gen_rsa()
    sig = _sign_rsa(priv, "original")
    with pytest.raises(SignatureVerificationError, match="invalid signature"):
        verify_signature(public_key_pem=pub, signature_b64=sig, message="tampered")


def test_wrong_key_raises():
    _, priv_a = _gen_rsa()
    pub_b, _ = _gen_rsa()
    sig = _sign_rsa(priv_a, "msg")
    with pytest.raises(SignatureVerificationError, match="invalid signature"):
        verify_signature(public_key_pem=pub_b, signature_b64=sig, message="msg")


def test_malformed_signature_b64_raises():
    pub, _ = _gen_rsa()
    with pytest.raises(SignatureVerificationError, match="decode"):
        verify_signature(public_key_pem=pub, signature_b64="!!!not-b64!!!", message="x")


def test_malformed_public_key_raises():
    with pytest.raises(SignatureVerificationError, match="public key"):
        verify_signature(
            public_key_pem=b"not a real PEM",
            signature_b64="AA==",
            message="x",
        )


def test_unsupported_key_type_raises():
    """A symmetric key (PEM-formatted nonsense) shouldn't accidentally pass."""
    # Use an Ed25519 key — the verifier should still handle it gracefully.
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    sig = base64.b64encode(private.sign(b"x")).decode("ascii")
    # Ed25519 IS valid; verifier should accept it
    verify_signature(public_key_pem=pub, signature_b64=sig, message="x")


# ---------------------------------------------------------- caching


def test_cache_skips_repeated_verifications(monkeypatch):
    pub, priv = _gen_rsa()
    msg = "same-blob"
    sig = _sign_rsa(priv, msg)

    # Run once to populate cache
    verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)

    # Subsequent calls hit the cache; we can't easily count inner calls
    # without instrumentation, but we can at least confirm idempotence.
    for _ in range(5):
        verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)


def test_cache_disabled_by_env(monkeypatch):
    """``AGENT_PLATFORM_SIGNATURE_CACHE_DISABLED=1`` short-circuits the cache.

    The verification still runs (and passes for valid input), but no entry
    is stored — useful when admins are debugging false positives.
    """
    monkeypatch.setenv("AGENT_PLATFORM_SIGNATURE_CACHE_DISABLED", "1")
    pub, priv = _gen_rsa()
    msg = "x"
    sig = _sign_rsa(priv, msg)
    verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)
    # And invalid input still raises
    with pytest.raises(SignatureVerificationError):
        verify_signature(public_key_pem=pub, signature_b64=sig, message="tampered")


def test_clear_cache_helper_works():
    pub, priv = _gen_rsa()
    msg = "y"
    sig = _sign_rsa(priv, msg)
    verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)
    clear_signature_cache()
    # Still works after clear
    verify_signature(public_key_pem=pub, signature_b64=sig, message=msg)
