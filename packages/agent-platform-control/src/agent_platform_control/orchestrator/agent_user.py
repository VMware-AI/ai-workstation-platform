"""Per-VM owner linux account derivation (Decision 1B).

Each VM cloud-init creates a single linux account named after the deployment's
owner. This module owns the two functions the orchestrator + cloud-init
template renderer need:

  * ``sanitize_username(owner_id)`` — coerce a portal owner id (which may be
    an email, contain dots, uppercase, spaces, CJK, etc.) into a POSIX-valid
    linux username that matches ``^[a-z_][a-z0-9_-]{0,30}$``.
  * ``derive_uid(owner_id)`` — deterministic UID in ``[1000, 50999]`` derived
    from the sanitized name's SHA-256 digest.

UID strategy (M1 expedient): pure hash modulo 50_000. Collision probability is
non-trivial at scale (~10% at 100 users, near-certain at 1000) — acceptable for
the M1 single-tenant demo. The forward path is a ``users.linux_uid`` column
populated by Keycloak/user-sync, at which point ``derive_uid`` becomes a
fallback for unprovisioned owners. See decision 1B (M1 → M2) in
``docs/architecture/02-vm-lifecycle.md``.
"""

from __future__ import annotations

import hashlib
import re

# POSIX useradd accepts ``[a-z_][a-z0-9_-]*``. Linux LOGIN_NAME_MAX is 32 chars;
# we cap at 31 to leave one byte of safety margin for tooling that null-terminates.
_MAX_LEN = 31
_VALID_BODY = re.compile(r"[a-z0-9_-]")
_LEADING_DIGIT = re.compile(r"^[0-9]")
_VALID_RESULT = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")

# UID range. 0-999 is reserved for system users on every distro we care about;
# starting at 1000 keeps us out of root + service-account territory. The 50_000
# slot count gives reasonable distribution without colliding with NSS-cached
# entries that some distros assign above 60_000.
UID_MIN = 1000
UID_MAX = UID_MIN + 49_999  # 50_999, inclusive
_UID_SPAN = UID_MAX - UID_MIN + 1


class AgentUserError(ValueError):
    """Raised when an owner_id can't be turned into a valid linux account."""


def sanitize_username(owner_id: str) -> str:
    """Coerce an arbitrary owner id into a POSIX-valid linux username.

    Steps:
      1. Lowercase.
      2. Replace any character outside ``[a-z0-9_-]`` with ``_``.
      3. Prefix ``u_`` if the result starts with a digit (linux requires
         ``[a-z_]`` as first char).
      4. Truncate to 31 characters.
      5. Raise ``AgentUserError`` if the result is empty or all underscores
         (no meaningful name to derive).

    Examples::

        sanitize_username("Alice.Wang@example.com") → "alice_wang_example_com"
        sanitize_username("123abc")                 → "u_123abc"
        sanitize_username("中文")                   → AgentUserError
    """
    if not owner_id:
        raise AgentUserError("owner_id is empty; cannot derive linux username")

    lowered = owner_id.lower()
    sanitized = "".join(c if _VALID_BODY.match(c) else "_" for c in lowered)

    # Reject if every character was stripped to ``_`` (no signal left).
    if not sanitized.replace("_", ""):
        raise AgentUserError(
            f"owner_id {owner_id!r} contains no characters that map to a valid "
            f"linux username; provision the user a stable ASCII login first"
        )

    if _LEADING_DIGIT.match(sanitized):
        sanitized = "u_" + sanitized

    sanitized = sanitized[:_MAX_LEN]

    if not _VALID_RESULT.match(sanitized):
        # Shouldn't happen given the steps above, but fail loud if a future
        # edit breaks the invariant.
        raise AgentUserError(
            f"sanitization produced {sanitized!r} which violates ^[a-z_][a-z0-9_-]{{0,30}}$"
        )

    return sanitized


def derive_uid(owner_id: str) -> int:
    """Deterministic UID in ``[UID_MIN, UID_MAX]`` derived from ``owner_id``.

    The hash is taken over the **sanitized** username so equivalent spellings
    (case differences, email vs login) map to the same UID and thus the same
    on-disk file ownership.

    Collision is possible — see module docstring. Callers should treat the
    result as a starting point that may need to be overridden by a DB-allocated
    UID when one exists for this user (post-Keycloak).
    """
    username = sanitize_username(owner_id)  # also handles empty-input check
    digest = hashlib.sha256(username.encode("utf-8")).digest()
    # First 4 bytes give a uniform 32-bit value; modulo span keeps it in range.
    offset = int.from_bytes(digest[:4], "big") % _UID_SPAN
    return UID_MIN + offset
