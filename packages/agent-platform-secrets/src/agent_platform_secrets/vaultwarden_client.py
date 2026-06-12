"""Minimal Vaultwarden / Bitwarden-compatible REST client.

Vaultwarden implements the Bitwarden API. We use the smallest slice
that lets the control plane fetch a secret at startup:

1. POST /identity/connect/token (grant_type=client_credentials)
   → access_token (JWT, 1h TTL)
2. GET /api/sync
   → catalogue of folders + ciphers. Filter to ``folders[].name == path``
     and ``ciphers[].name == name``, return the cipher's ``login.password``.

This skips the full cipher-level crypto: Bitwarden encrypts every
cipher field with the user's master key, but the password field is
served as plaintext when the org-side encryption isn't configured.
For a service account with read-only access to a single folder,
that's what we get back. If a deployment needs end-to-end client-side
encryption later, swap this client for the Bitwarden Rust SDK; the
:class:`SecretResolver` Protocol stays the same.

Threats this client does NOT defend against:

* MITM — relies on TLS. Configure with HTTPS-only base URL.
* Token leak in logs — caller should redact ``access_token`` from
  any structured logger middleware before logging the response.

Threats it does defend against:

* SSRF via ``base_url`` — caller's responsibility to pin (config,
  not runtime input). We never call user-supplied URLs.
* Stale token — 60s skew before refresh.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class VaultwardenError(RuntimeError):
    """Base for all Vaultwarden client failures."""


class VaultwardenAuthError(VaultwardenError):
    """Raised on a 401/403 from the identity endpoint."""


class VaultwardenNotFoundError(VaultwardenError):
    """Raised when the requested folder/name pair doesn't resolve to a cipher."""


@dataclass(slots=True)
class _CachedToken:
    # repr=False: the value is a live bearer JWT — it must never surface
    # in %r logging, assertion diffs, or debugger output.
    value: str = field(repr=False)
    expires_at: float  # monotonic seconds


@dataclass(slots=True)
class VaultwardenClient:
    """Sync-friendly client for one Vaultwarden deployment.

    ``base_url`` should NOT include a trailing slash. The identity
    endpoint lives at ``{base_url}/identity`` and the data API at
    ``{base_url}/api`` per Bitwarden convention.

    Auth is service-account style: ``client_id`` and ``client_secret``
    come from a Vaultwarden "API key" generated for an org member
    (see Vaultwarden admin docs).
    """

    base_url: str
    client_id: str
    # repr=False on the secret-bearing fields: any %r log line or pytest
    # assertion diff over the client would otherwise leak them verbatim.
    client_secret: str = field(repr=False)
    timeout_s: float = 10.0
    _token: _CachedToken | None = field(default=None, init=False, repr=False)
    _http: httpx.Client | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # SEC-18: this client relies on TLS to protect the client_secret and
        # the fetched secrets in transit (see "Threats" in the module docstring).
        # Refuse a plaintext http:// base_url unless it targets localhost
        # (dev/test against a local Vaultwarden).
        scheme, sep, rest = self.base_url.partition("://")
        host = rest.split("/", 1)[0].split(":", 1)[0] if sep else ""
        is_local = host in {"localhost", "127.0.0.1", "::1"}
        if scheme.lower() != "https" and not is_local:
            raise ValueError(
                f"VaultwardenClient base_url must be https:// (got {self.base_url!r}) — "
                "it carries the client_secret and fetched secrets in transit. "
                "Only localhost may use http:// for dev."
            )

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self.base_url.rstrip("/"),
                timeout=self.timeout_s,
            )
        return self._http

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Single chokepoint for HTTP I/O.

        Translates httpx transport failures (DNS, connect refused/timeout,
        TLS, read timeout) into :class:`VaultwardenError` so every caller
        sees only the documented exception family — a transient Vaultwarden
        outage must never leak a raw ``httpx.ConnectError`` upstream.
        """
        try:
            return self._client().request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise VaultwardenError(
                f"Vaultwarden {method} {path} failed in transport: {exc}. "
                "Check the base_url and network reachability of the Vaultwarden host."
            ) from exc

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> VaultwardenClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ auth

    def _login(self) -> str:
        response = self._request(
            "POST",
            "/identity/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "api",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code in (401, 403):
            raise VaultwardenAuthError(
                f"identity returned {response.status_code}: check client_id/secret"
            )
        if response.status_code >= 400:
            raise VaultwardenError(f"identity returned HTTP {response.status_code}")
        body: Mapping[str, Any] = response.json()
        token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not isinstance(token, str) or not token:
            raise VaultwardenError("identity response missing access_token")
        # Refresh 60s before the server says we expire, to absorb clock skew.
        ttl = int(expires_in) if isinstance(expires_in, (int, float)) else 3600
        self._token = _CachedToken(value=token, expires_at=time.monotonic() + max(60, ttl - 60))
        return token

    def _auth_header(self) -> dict[str, str]:
        token = self._token
        if token is None or token.expires_at <= time.monotonic():
            return {"Authorization": f"Bearer {self._login()}"}
        return {"Authorization": f"Bearer {token.value}"}

    # ----------------------------------------------------------------- fetch

    def fetch_password(self, *, folder: str, name: str) -> str:
        """Return the ``login.password`` of the cipher named ``name`` in ``folder``.

        Folder is matched case-insensitively. An empty ``folder`` means
        look for a top-level (no folder) cipher.

        Raises :class:`VaultwardenNotFoundError` if no matching cipher
        exists, :class:`VaultwardenAuthError` on credential failure,
        :class:`VaultwardenError` on transport / parse / API errors.
        """
        response = self._request("GET", "/api/sync", headers=self._auth_header())
        if response.status_code == 401:
            # Stale cached token despite the TTL guard — refresh once.
            self._token = None
            response = self._request("GET", "/api/sync", headers=self._auth_header())
        if response.status_code >= 400:
            raise VaultwardenError(f"sync returned HTTP {response.status_code}")
        body = response.json()

        # Resolve folder ID. Bitwarden sync ships {Folders: [{Id, Name}], Ciphers: [...]}.
        # Vaultwarden mirrors this shape; field casing is PascalCase.
        folder_id: str | None = None
        if folder:
            folders = body.get("Folders") or body.get("folders") or []
            for entry in folders:
                if (
                    isinstance(entry, Mapping)
                    and (entry.get("Name") or entry.get("name", "")).lower() == folder.lower()
                ):
                    folder_id = entry.get("Id") or entry.get("id")
                    break
            if folder_id is None:
                raise VaultwardenNotFoundError(f"folder {folder!r} not found")

        ciphers = body.get("Ciphers") or body.get("ciphers") or []
        for cipher in ciphers:
            if not isinstance(cipher, Mapping):
                continue
            cname = cipher.get("Name") or cipher.get("name")
            cfolder = cipher.get("FolderId") or cipher.get("folderId")
            if cname != name:
                continue
            if folder and cfolder != folder_id:
                continue
            if not folder and cfolder not in (None, ""):
                continue
            login = cipher.get("Login") or cipher.get("login") or {}
            if isinstance(login, Mapping):
                password = login.get("Password") or login.get("password")
                if isinstance(password, str) and password:
                    return password
            raise VaultwardenNotFoundError(f"cipher {name!r} found but has no login.password field")
        raise VaultwardenNotFoundError(f"cipher {name!r} not found in folder {folder!r}")

    # ---------------------------------------------------------- internal lookup

    def _fetch_sync(self) -> Mapping[str, Any]:
        """One-shot sync fetch — used by write paths to resolve folder/cipher IDs."""
        response = self._request("GET", "/api/sync", headers=self._auth_header())
        if response.status_code == 401:
            self._token = None
            response = self._request("GET", "/api/sync", headers=self._auth_header())
        if response.status_code >= 400:
            raise VaultwardenError(f"sync returned HTTP {response.status_code}")
        return response.json()

    @staticmethod
    def _find_folder_id(body: Mapping[str, Any], folder: str) -> str | None:
        folders = body.get("Folders") or body.get("folders") or []
        for entry in folders:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("Name") or entry.get("name", "")
            if name.lower() == folder.lower():
                return entry.get("Id") or entry.get("id")
        return None

    @staticmethod
    def _find_cipher_id(body: Mapping[str, Any], folder_id: str | None, name: str) -> str | None:
        for cipher in body.get("Ciphers") or body.get("ciphers") or []:
            if not isinstance(cipher, Mapping):
                continue
            if (cipher.get("Name") or cipher.get("name")) != name:
                continue
            cfolder = cipher.get("FolderId") or cipher.get("folderId")
            if folder_id is None:
                if cfolder in (None, ""):
                    return cipher.get("Id") or cipher.get("id")
            elif cfolder == folder_id:
                return cipher.get("Id") or cipher.get("id")
        return None

    def _create_folder(self, folder: str) -> str:
        response = self._request(
            "POST",
            "/api/folders",
            json={"name": folder},
            headers={**self._auth_header(), "Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            raise VaultwardenError(f"folder create returned HTTP {response.status_code}")
        body = response.json()
        folder_id = body.get("Id") or body.get("id")
        if not isinstance(folder_id, str):
            raise VaultwardenError("folder create response missing Id")
        return folder_id

    # ----------------------------------------------------------------- writes

    def put_password(self, *, folder: str, name: str, value: str) -> str:
        """Create or update a cipher named ``name`` in ``folder`` with password ``value``.

        Returns the cipher ID. Creates the folder if it doesn't exist yet.
        Auth + transport errors surface as :class:`VaultwardenAuthError` /
        :class:`VaultwardenError`.
        """
        body = self._fetch_sync()
        folder_id = self._find_folder_id(body, folder) if folder else None
        if folder and folder_id is None:
            folder_id = self._create_folder(folder)

        existing = self._find_cipher_id(body, folder_id, name)
        payload = {
            "type": 1,  # Bitwarden login
            "name": name,
            "folderId": folder_id,
            "login": {"username": "", "password": value},
            "notes": None,
        }
        headers = {**self._auth_header(), "Content-Type": "application/json"}
        if existing:
            response = self._request(
                "PUT", f"/api/ciphers/{existing}", json=payload, headers=headers
            )
        else:
            response = self._request("POST", "/api/ciphers", json=payload, headers=headers)
        if response.status_code in (401, 403):
            raise VaultwardenAuthError(
                f"cipher write returned {response.status_code}: token may be stale"
            )
        if response.status_code >= 400:
            raise VaultwardenError(f"cipher write returned HTTP {response.status_code}")
        out = response.json()
        cipher_id = out.get("Id") or out.get("id")
        if not isinstance(cipher_id, str):
            raise VaultwardenError("cipher write response missing Id")
        return cipher_id

    def delete_password(self, *, folder: str, name: str) -> None:
        """Idempotent delete: a missing cipher is treated as already gone."""
        body = self._fetch_sync()
        folder_id = self._find_folder_id(body, folder) if folder else None
        if folder and folder_id is None:
            return  # folder absent → nothing to delete
        cipher_id = self._find_cipher_id(body, folder_id, name)
        if cipher_id is None:
            return  # cipher absent → idempotent no-op
        response = self._request("DELETE", f"/api/ciphers/{cipher_id}", headers=self._auth_header())
        if response.status_code in (401, 403):
            raise VaultwardenAuthError(f"cipher delete returned {response.status_code}")
        if response.status_code not in (200, 204, 404):
            raise VaultwardenError(f"cipher delete returned HTTP {response.status_code}")

    def list_folder(self, folder: str) -> list[str]:
        """Return cipher names inside ``folder``. Empty list when folder absent."""
        body = self._fetch_sync()
        folder_id = self._find_folder_id(body, folder) if folder else None
        if folder and folder_id is None:
            return []
        names: list[str] = []
        for cipher in body.get("Ciphers") or body.get("ciphers") or []:
            if not isinstance(cipher, Mapping):
                continue
            cfolder = cipher.get("FolderId") or cipher.get("folderId")
            match = (cfolder == folder_id) if folder else (cfolder in (None, ""))
            if not match:
                continue
            cname = cipher.get("Name") or cipher.get("name")
            if isinstance(cname, str):
                names.append(cname)
        return names
