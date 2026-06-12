"""Deployment agent token helpers.

The database stores only encrypted token material plus a sha256 lookup hash.
The worker decrypts at clone time because cloud-init must receive the plaintext
token once, inside the guest.
"""

from __future__ import annotations

import hashlib
import uuid

from cryptography.fernet import Fernet

from ..config import get_settings


def generate_user_token() -> str:
    """One opaque token per VM."""
    return f"tok_{uuid.uuid4().hex}"


def hash_user_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    return Fernet(get_settings().deployment_token_fernet_key.encode("ascii"))


def encrypt_user_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_user_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
