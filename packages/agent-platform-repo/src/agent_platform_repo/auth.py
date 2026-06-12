"""Credential loader for devpi access.

Reads from environment variables only. Hard-coding secrets is forbidden by
the repo CLAUDE.md security rules; a future task will fetch these from
Vaultwarden (C18) instead of the process environment.

Env vars:
    DEVPI_USER     — devpi user (default: ``root``, devpi's bootstrap user)
    DEVPI_PASSWORD — devpi password (no default; required for write ops)
    DEVPI_URL      — devpi server base URL (default: ``http://127.0.0.1:3141``)
    DEVPI_INDEX    — target index path (default: ``root/agent-platform``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:3141"
DEFAULT_USER = "root"
DEFAULT_INDEX = "root/agent-platform"


class MissingCredentialError(RuntimeError):
    """Raised when a write operation is requested but no password is set."""


@dataclass(frozen=True)
class Credentials:
    """Immutable devpi connection bundle."""

    url: str
    user: str
    # repr=False: the password must never reach logs or assertion
    # diffs via the default dataclass repr.
    password: str | None = field(repr=False)
    index: str

    @property
    def index_url(self) -> str:
        """Full URL of the target index (no trailing slash)."""
        return f"{self.url.rstrip('/')}/{self.index}"

    @property
    def simple_url(self) -> str:
        """PEP 503 ``+simple`` URL — pass to ``pip install --index-url``."""
        return f"{self.index_url}/+simple/"


def load_credentials(*, require_password: bool = False) -> Credentials:
    """Build a :class:`Credentials` from process env.

    Args:
        require_password: if True and no password is set, raise
            :class:`MissingCredentialError` instead of returning ``None``.

    TODO(C18): replace env reads with a Vaultwarden lookup once
    ``agent-platform-secrets`` exposes a stable client.
    """
    url = os.environ.get("DEVPI_URL", DEFAULT_URL)
    user = os.environ.get("DEVPI_USER", DEFAULT_USER)
    password = os.environ.get("DEVPI_PASSWORD") or None
    index = os.environ.get("DEVPI_INDEX", DEFAULT_INDEX)

    if require_password and password is None:
        raise MissingCredentialError(
            "DEVPI_PASSWORD is not set; required for write operations. "
            "Export it in your shell or load it from Vaultwarden (C18) before retry."
        )

    # SEC-10: whenever a password is present, the CLI's _client() attaches
    # Basic Auth to EVERY request — including reads — so any credentialed call
    # to a plaintext http:// non-localhost host ships the password in the
    # clear. Refuse the combination outright (fail-closed); the default
    # 127.0.0.1 dev URL and a TLS-terminating Caddy in front of a remote devpi
    # both stay fine.
    if password is not None and _is_insecure_remote(url):
        raise MissingCredentialError(
            f"DEVPI_URL {url!r} is plaintext http:// to a non-localhost host; "
            "DEVPI_PASSWORD would be sent in the clear (Basic Auth is attached "
            "to every request, reads included). Use https:// (front devpi with "
            "TLS, e.g. Caddy), target localhost for dev, or unset "
            "DEVPI_PASSWORD for read-only commands."
        )

    return Credentials(url=url, user=user, password=password, index=index)


def _is_insecure_remote(url: str) -> bool:
    """True for a plaintext http:// URL whose host is not localhost.

    Uses ``urlsplit(url).hostname`` so bracketed IPv6 literals parse
    correctly (``http://[::1]:3141`` → host ``::1``, not ``[``); hostname is
    also lowercased by urlsplit. An unparseable host fails closed (treated
    as remote).
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != "http":
        return False
    host = parts.hostname
    if host is None:
        return True
    return host not in {"localhost", "127.0.0.1", "::1"}
