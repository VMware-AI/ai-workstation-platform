"""C18 agent-platform-secrets — control-plane secret resolution.

Two surfaces:

* ``SecretReference`` — parses a ``vault://`` / ``env://`` / literal
  string into a tagged enum the resolver can dispatch on.
* ``SecretResolver`` Protocol + two implementations:
    * :class:`EnvSecretResolver` — handles literals + ``env://VAR``,
      no external dependencies. Useful for dev + tests.
    * :class:`VaultwardenSecretResolver` — calls Vaultwarden's
      Bitwarden-compatible REST API to fetch a cipher.

Config consumers (agent-platform-control's ``config.py``, etc.) replace
plaintext field values with references like ``vault://nsx/api-key``,
then ``resolver.resolve(ref)`` returns the plaintext at startup.
This is Task 1.20.2's "delete config.yaml plaintext" foothold.
"""

from .reference import SecretReference, SecretReferenceError, parse_reference
from .resolver import (
    EnvSecretResolver,
    SecretResolver,
    SecretResolverError,
    VaultwardenSecretResolver,
    make_resolver,
)
from .vaultwarden_client import (
    VaultwardenAuthError,
    VaultwardenClient,
    VaultwardenError,
    VaultwardenNotFoundError,
)

__all__ = [
    "EnvSecretResolver",
    "SecretReference",
    "SecretReferenceError",
    "SecretResolver",
    "SecretResolverError",
    "VaultwardenAuthError",
    "VaultwardenClient",
    "VaultwardenError",
    "VaultwardenNotFoundError",
    "VaultwardenSecretResolver",
    "make_resolver",
    "parse_reference",
]
