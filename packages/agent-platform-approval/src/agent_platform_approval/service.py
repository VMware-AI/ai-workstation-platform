"""DB-backed approval service.

Every state transition writes one audit row in the same SQLAlchemy session,
so partial-failure cannot leave a request with the new state but no audit
entry (or vice versa).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .errors import ConflictError, RequestNotFoundError
from .models import AuditEvent, EventType, Request
from .state import State, transition


class ApprovalService:
    """Thin transaction script around the approval state machine.

    The caller owns the ``Session`` and ``session.commit()`` — that keeps the
    service composable with C1 control-plane request handlers that bundle
    several operations in one HTTP request.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    # ------------------------------------------------------------------ read

    def get(self, request_id: int) -> Request:
        req = self._s.get(Request, request_id)
        if req is None:
            raise RequestNotFoundError(
                f"Approval request {request_id} not found. "
                f"List pending via ApprovalService.list(state=State.PENDING)."
            )
        return req

    def list(
        self,
        *,
        state: State | None = None,
        requester: str | None = None,
        limit: int = 50,
    ) -> Sequence[Request]:
        """List requests, newest first. Always paginated (default 50)."""
        stmt = select(Request).order_by(Request.created_at.desc()).limit(limit)
        if state is not None:
            stmt = stmt.where(Request.state == state)
        if requester is not None:
            stmt = stmt.where(Request.requester == requester)
        return self._s.execute(stmt).scalars().all()

    # ----------------------------------------------------------------- write

    def submit(self, *, requester: str, package: str, justification: str) -> Request:
        """Create a new request in PENDING + emit a SUBMITTED audit event."""
        req = Request(
            requester=requester,
            package=package,
            justification=justification,
            state=State.PENDING,
        )
        self._s.add(req)
        self._s.flush()  # populate req.id for the audit row
        self._s.add(
            AuditEvent(
                request_id=req.id,
                event_type=EventType.SUBMITTED,
                actor=requester,
                payload={"package": package},
            )
        )
        return req

    def approve(self, request_id: int, *, admin: str, reason: str | None = None) -> Request:
        return self._decide(
            request_id, target=State.APPROVED, event=EventType.APPROVED, admin=admin, reason=reason
        )

    def reject(self, request_id: int, *, admin: str, reason: str) -> Request:
        if not reason or not reason.strip():
            # Reject reasons are required — without one, audit history is useless
            # for "why was this user denied" questions.
            raise ValueError("reject() requires a non-empty reason")
        return self._decide(
            request_id, target=State.REJECTED, event=EventType.REJECTED, admin=admin, reason=reason
        )

    def comment(self, request_id: int, *, admin: str, text: str) -> AuditEvent:
        """Attach a free-form note to a request without changing state."""
        req = self.get(request_id)
        event = AuditEvent(
            request_id=req.id,
            event_type=EventType.COMMENT,
            actor=admin,
            payload={"text": text},
        )
        self._s.add(event)
        return event

    # ---------------------------------------------------------------- helper

    def _decide(
        self,
        request_id: int,
        *,
        target: State,
        event: EventType,
        admin: str,
        reason: str | None,
    ) -> Request:
        req = self.get(request_id)
        transition(req.state, target)  # raises InvalidTransitionError if terminal
        # Concurrency guard: the decision is a conditional UPDATE on
        # state='pending', so of two racing deciders only one matches the row.
        # The loser sees rowcount == 0 and gets ConflictError instead of
        # silently overwriting the winner's decision (read-then-write race).
        result = self._s.execute(
            update(Request)
            .where(Request.id == request_id, Request.state == State.PENDING)
            .values(
                state=target,
                decided_at=datetime.now(UTC),
                decided_by=admin,
                decision_reason=reason,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise ConflictError(
                f"Approval request {request_id} was decided by another writer "
                f"while this decision was in flight. Re-fetch it via "
                f"ApprovalService.get({request_id}) to see the final state; "
                f"do not retry the decision blindly."
            )
        # Audit row only on the success path — a lost race must not record a
        # decision event that never took effect.
        self._s.add(
            AuditEvent(
                request_id=req.id,
                event_type=event,
                actor=admin,
                payload={"reason": reason} if reason else {},
            )
        )
        # The UPDATE bypassed the unit of work — expire the cached instance so
        # the caller reads the decided values back from the row, not stale state.
        self._s.expire(req)
        return req
