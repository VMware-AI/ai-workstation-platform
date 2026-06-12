"""Parse a config field value into a :class:`SecretReference`.

The wire shape on disk is a single string. We support three forms:

* ``"plain text value"``          → :class:`LiteralReference`
* ``"env://VAR_NAME"``            → :class:`EnvReference`
* ``"vault://<path>/<name>"``     → :class:`VaultReference`

Anything starting with a scheme we don't recognise raises
:class:`SecretReferenceError` so a typo fails the operator startup
rather than silently shipping ``vlt://foo`` as a literal password.
"""

from __future__ import annotations

from dataclasses import dataclass


class SecretReferenceError(ValueError):
    """Raised when a string looks like a scheme reference but the scheme
    is not one we recognise."""


@dataclass(frozen=True, slots=True)
class LiteralReference:
    """The value is not a reference — it's the literal secret."""

    value: str


@dataclass(frozen=True, slots=True)
class EnvReference:
    """``env://VAR_NAME`` — resolved from the process environment."""

    var: str


@dataclass(frozen=True, slots=True)
class VaultReference:
    """``vault://<path>/<name>`` — resolved from Vaultwarden.

    Convention: ``path`` is the folder name (e.g. ``"nsx"``); ``name``
    is the item name within that folder. Together they identify one
    cipher. Splitting on the LAST slash so paths can contain slashes
    for nested folders if needed later.
    """

    path: str
    name: str

    @property
    def full(self) -> str:
        return f"{self.path}/{self.name}" if self.path else self.name


SecretReference = LiteralReference | EnvReference | VaultReference


# We only treat strings that LOOK like a scheme reference (contain "://")
# as references. Anything else is a literal — this matches operators'
# expectation that an existing plaintext config keeps working.
_KNOWN_SCHEMES = frozenset({"env", "vault"})


def parse_reference(raw: str) -> SecretReference:
    """Classify ``raw`` into one of the three reference variants."""
    if "://" not in raw:
        return LiteralReference(raw)
    scheme, _, rest = raw.partition("://")
    scheme = scheme.lower()
    if scheme not in _KNOWN_SCHEMES:
        # Deliberately do NOT echo the full value: a literal secret that
        # happens to contain "://" would otherwise land verbatim in the
        # operator-facing startup error / logs (PR #220 review M-2).
        raise SecretReferenceError(
            f"unknown secret-reference scheme {scheme!r}: "
            "expected env:// or vault:// (full value redacted in case it is "
            "a literal secret)"
        )
    if scheme == "env":
        if not rest:
            raise SecretReferenceError("env:// reference is missing the variable name")
        return EnvReference(var=rest)
    # scheme == "vault"
    if not rest:
        raise SecretReferenceError("vault:// reference is missing the path/name")
    if "/" in rest:
        path, _, name = rest.rpartition("/")
        return VaultReference(path=path, name=name)
    return VaultReference(path="", name=rest)
