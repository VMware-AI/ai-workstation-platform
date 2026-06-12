"""Agent Platform scale bundle (C9).

Pack a release tree into a single tar.zst artifact, then sign and verify it with
cosign. Public API:

- ``pack(src, out)`` — produce ``out.tar.zst`` + ``out.tar.zst.sha256``.
- ``sign(artifact, key, sig_out)`` — call ``cosign sign-blob``.
- ``verify(artifact, key, sig)`` — call ``cosign verify-blob``.
"""

from .bundle import BundleError, pack, sha256_file
from .cosign import CosignError, CosignNotFoundError, SignatureMismatchError, sign, verify

__version__ = "0.1.0"
__all__ = [
    "BundleError",
    "CosignError",
    "CosignNotFoundError",
    "SignatureMismatchError",
    "pack",
    "sha256_file",
    "sign",
    "verify",
]
