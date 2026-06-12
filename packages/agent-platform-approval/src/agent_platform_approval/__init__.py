"""Agent Platform approval (C13).

Approval workflow for user-submitted requests (e.g. "give me a new agent VM
with quota X"). M1 surface is intentionally small:

- ``State`` enum + ``transition()`` — pure state machine, no I/O
- SQLAlchemy ``Request`` / ``AuditEvent`` models
- ``ApprovalService`` — DB-backed orchestrator that records every transition
  as an audit row, in the same DB transaction
"""

from .errors import ApprovalError, ConflictError, InvalidTransitionError, RequestNotFoundError
from .models import AuditEvent, Base, EventType, Request, State
from .service import ApprovalService
from .state import ALLOWED_TRANSITIONS, transition

__version__ = "0.1.0"
__all__ = [
    "ALLOWED_TRANSITIONS",
    "ApprovalError",
    "ApprovalService",
    "AuditEvent",
    "Base",
    "ConflictError",
    "EventType",
    "InvalidTransitionError",
    "Request",
    "RequestNotFoundError",
    "State",
    "transition",
]
