"""Exception hierarchy for approval operations."""

from __future__ import annotations


class ApprovalError(Exception):
    """Base for all approval workflow errors."""


class RequestNotFoundError(ApprovalError):
    """No request with the supplied id exists."""


class InvalidTransitionError(ApprovalError):
    """The requested state transition is not allowed by the state machine.

    Raised both at the pure-function ``transition()`` layer and surfaced by the
    DB service when an admin tries to approve/reject a request that is no
    longer pending.
    """


class ConflictError(ApprovalError):
    """Another writer mutated the row between read and write (lost update)."""
