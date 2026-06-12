"""Pure state machine — no DB, no I/O. Tested in isolation."""

from __future__ import annotations

from enum import StrEnum

from .errors import InvalidTransitionError


class State(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# Explicit transition table: ``from`` -> set of allowed ``to`` states.
# Terminal states (APPROVED, REJECTED) have empty sets — that is the whole
# point of the M1 design: no re-opens, no reversals, no soft-deletes. If we
# need to re-process, create a new request that references the old id.
ALLOWED_TRANSITIONS: dict[State, frozenset[State]] = {
    State.PENDING: frozenset({State.APPROVED, State.REJECTED}),
    State.APPROVED: frozenset(),
    State.REJECTED: frozenset(),
}


def transition(current: State | str, target: State | str) -> State:
    """Return ``target`` if ``current -> target`` is allowed, else raise.

    Identity transitions (``current == target``) are rejected — they are
    almost always a bug in the caller, never a useful no-op.

    Both inputs are coerced through ``State(...)``. The State column is
    stored as ``String(16)`` in Postgres / SQLite, so a fresh SQLAlchemy
    session loads ``Request.state`` back as a plain ``str`` rather than
    the in-memory enum; coercing here keeps every caller from having
    to remember that subtlety.
    """
    current = State(current)
    target = State(target)
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS[current])
        hint = repr(allowed) if allowed else "terminal (no transitions)"
        raise InvalidTransitionError(
            f"Cannot transition from {current.value!r} to {target.value!r}. "
            f"Allowed from {current.value!r}: {hint}"
        )
    return target
