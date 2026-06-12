"""SecretResolver Protocol + two concrete implementations.

Callers wire ONE resolver into their config layer and call
``resolver.resolve(reference)``. The resolver handles all three
reference variants by dispatching on type; per-variant resolution
logic lives here, not in the caller.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

from .reference import (
    EnvReference,
    LiteralReference,
    SecretReference,
)
from .vaultwarden_client import (
    VaultwardenAuthError,
    VaultwardenClient,
    VaultwardenError,
    VaultwardenNotFoundError,
)

logger = logging.getLogger(__name__)


class SecretResolverError(RuntimeError):
    """Raised when a reference cannot be resolved (missing env var, missing
    cipher, etc.)."""


class SecretResolver(Protocol):
    """Resolve a parsed :class:`SecretReference` to its plaintext value."""

    def resolve(self, reference: SecretReference) -> str: ...


@dataclass(slots=True)
class EnvSecretResolver:
    """Handles ``LiteralReference`` + ``EnvReference``.

    Rejects ``VaultReference`` with a clear error so misconfigured
    deployments (vault://… in config but no Vaultwarden wired) fail
    fast at startup rather than treating the vault URL as a literal.
    """

    def resolve(self, reference: SecretReference) -> str:
        if isinstance(reference, LiteralReference):
            return reference.value
        if isinstance(reference, EnvReference):
            value = os.environ.get(reference.var)
            if value is None:
                raise SecretResolverError(
                    f"env var {reference.var!r} is not set (referenced by env://{reference.var})"
                )
            return value
        # VaultReference reaches here when this resolver is wired by
        # mistake. Loud failure beats silent fallback.
        raise SecretResolverError(
            "EnvSecretResolver cannot resolve vault:// references — "
            "use VaultwardenSecretResolver instead"
        )


@dataclass(slots=True)
class VaultwardenSecretResolver:
    """Handles all three reference variants — env/literal are pass-through,
    vault:// fetches from the wrapped :class:`VaultwardenClient`.

    Keep the client lifetime owned by the caller (``with`` block or a
    long-lived application-level instance); this resolver does not
    close it.
    """

    client: VaultwardenClient

    def resolve(self, reference: SecretReference) -> str:
        # Reuse the env logic for the simple cases so behaviour matches.
        if isinstance(reference, (LiteralReference, EnvReference)):
            return EnvSecretResolver().resolve(reference)
        # VaultReference
        try:
            return self.client.fetch_password(
                folder=reference.path,
                name=reference.name,
            )
        except VaultwardenNotFoundError as exc:
            raise SecretResolverError(
                f"vault://{reference.full} not found in Vaultwarden: {exc}"
            ) from exc
        except VaultwardenAuthError as exc:
            raise SecretResolverError(
                f"vault://{reference.full} could not be resolved — Vaultwarden auth "
                f"failed: {exc}. Check the configured client_id / client_secret."
            ) from exc
        except VaultwardenError as exc:
            # Catch-all for transport / API failures so callers only ever
            # see the SecretResolverError promised by the Protocol.
            raise SecretResolverError(
                f"vault://{reference.full} could not be fetched from Vaultwarden: {exc}"
            ) from exc


def make_resolver(
    *,
    base_url: str | None,
    client_id: str,
    client_secret: str,
    timeout_s: float = 10.0,
) -> SecretResolver:
    """Build the right resolver for a service's bootstrap (H-11, #212).

    No ``base_url`` → :class:`EnvSecretResolver` (dev/.env posture, current
    behaviour). With a ``base_url`` → :class:`VaultwardenSecretResolver`;
    missing credentials then are a misconfiguration and fail loudly here,
    not as a confusing 401 at first resolve. Shared by control and gateway
    so the two can't drift on construction rules.
    """
    if not base_url:
        return EnvSecretResolver()
    if not client_id:
        raise ValueError("Vaultwarden base_url is set but client_id is empty")
    if not client_secret:
        raise ValueError("Vaultwarden base_url is set but client_secret is empty")
    return VaultwardenSecretResolver(
        client=VaultwardenClient(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            timeout_s=timeout_s,
        )
    )
