from __future__ import annotations

import pytest
from agent_platform_approval import (
    ApprovalService,
    ConflictError,
    EventType,
    InvalidTransitionError,
    RequestNotFoundError,
    State,
)
from sqlalchemy import text
from sqlalchemy.orm import Session


@pytest.mark.unit
def test_submit_creates_pending_with_audit(svc: ApprovalService, session: Session) -> None:
    req = svc.submit(requester="alice", package="agent-vm-small", justification="POC for team")
    session.flush()

    assert req.state == State.PENDING
    assert req.created_at is not None
    assert req.decided_at is None
    assert len(req.audit_events) == 1
    assert req.audit_events[0].event_type == EventType.SUBMITTED
    assert req.audit_events[0].actor == "alice"
    assert req.audit_events[0].payload == {"package": "agent-vm-small"}


@pytest.mark.unit
def test_approve_pending_request(svc: ApprovalService) -> None:
    req = svc.submit(requester="alice", package="x", justification="j")
    approved = svc.approve(req.id, admin="bob", reason="LGTM")

    assert approved.state == State.APPROVED
    assert approved.decided_by == "bob"
    assert approved.decision_reason == "LGTM"
    assert approved.decided_at is not None
    assert [e.event_type for e in approved.audit_events] == [
        EventType.SUBMITTED,
        EventType.APPROVED,
    ]


@pytest.mark.unit
def test_reject_pending_request(svc: ApprovalService) -> None:
    req = svc.submit(requester="alice", package="x", justification="j")
    rejected = svc.reject(req.id, admin="bob", reason="quota exceeded")

    assert rejected.state == State.REJECTED
    assert rejected.decision_reason == "quota exceeded"


@pytest.mark.unit
def test_reject_requires_reason(svc: ApprovalService) -> None:
    req = svc.submit(requester="alice", package="x", justification="j")
    with pytest.raises(ValueError, match="non-empty reason"):
        svc.reject(req.id, admin="bob", reason="")
    with pytest.raises(ValueError, match="non-empty reason"):
        svc.reject(req.id, admin="bob", reason="   ")


@pytest.mark.unit
def test_cannot_approve_already_approved(svc: ApprovalService) -> None:
    req = svc.submit(requester="alice", package="x", justification="j")
    svc.approve(req.id, admin="bob")
    with pytest.raises(InvalidTransitionError):
        svc.approve(req.id, admin="bob")


@pytest.mark.unit
def test_cannot_reject_already_approved(svc: ApprovalService) -> None:
    """The state machine is the *only* thing keeping admins from quietly
    flipping someone's approval to a rejection after the fact."""
    req = svc.submit(requester="alice", package="x", justification="j")
    svc.approve(req.id, admin="bob")
    with pytest.raises(InvalidTransitionError):
        svc.reject(req.id, admin="bob", reason="changed my mind")


@pytest.mark.unit
def test_decide_lost_race_raises_conflict(svc: ApprovalService, session: Session) -> None:
    """Concurrency guard: the decision UPDATE is conditional on state='pending'.

    Simulate a concurrent decider by flipping the DB row out-of-band — the
    in-memory object still believes the request is pending, so the losing
    writer must get ConflictError from the rowcount guard, not a silent
    overwrite.
    """
    req = svc.submit(requester="alice", package="x", justification="j")
    session.flush()
    session.connection().execute(
        text("UPDATE approval_requests SET state='approved' WHERE id=:id"),
        {"id": req.id},
    )

    with pytest.raises(ConflictError, match="decided by another writer"):
        svc.approve(req.id, admin="bob")


@pytest.mark.unit
def test_decide_lost_race_writes_no_audit_row(svc: ApprovalService, session: Session) -> None:
    """A lost race must not log a decision audit event (success path only)."""
    req = svc.submit(requester="alice", package="x", justification="j")
    session.flush()
    session.connection().execute(
        text("UPDATE approval_requests SET state='approved' WHERE id=:id"),
        {"id": req.id},
    )

    with pytest.raises(ConflictError):
        svc.reject(req.id, admin="bob", reason="too late")

    events = (
        session.execute(
            text("SELECT event_type FROM approval_audit_events WHERE request_id=:id"),
            {"id": req.id},
        )
        .scalars()
        .all()
    )
    assert events == [EventType.SUBMITTED.value]


@pytest.mark.unit
def test_get_missing_raises_teaching_error(svc: ApprovalService) -> None:
    with pytest.raises(RequestNotFoundError, match="not found"):
        svc.get(99999)


@pytest.mark.unit
def test_list_filters_by_state(svc: ApprovalService) -> None:
    a = svc.submit(requester="alice", package="x", justification="j")
    svc.submit(requester="bob", package="y", justification="j")
    svc.approve(a.id, admin="root")

    pending = svc.list(state=State.PENDING)
    approved = svc.list(state=State.APPROVED)

    assert len(pending) == 1 and pending[0].requester == "bob"
    assert len(approved) == 1 and approved[0].requester == "alice"


@pytest.mark.unit
def test_list_filters_by_requester(svc: ApprovalService) -> None:
    svc.submit(requester="alice", package="x", justification="j")
    svc.submit(requester="bob", package="y", justification="j")

    assert {r.requester for r in svc.list(requester="alice")} == {"alice"}


@pytest.mark.unit
def test_list_respects_limit(svc: ApprovalService) -> None:
    for i in range(10):
        svc.submit(requester=f"u{i}", package="x", justification="j")
    assert len(svc.list(limit=3)) == 3


@pytest.mark.unit
def test_comment_does_not_change_state(svc: ApprovalService) -> None:
    req = svc.submit(requester="alice", package="x", justification="j")
    svc.comment(req.id, admin="bob", text="need more info")

    refetched = svc.get(req.id)
    assert refetched.state == State.PENDING
    assert refetched.audit_events[-1].event_type == EventType.COMMENT
    assert refetched.audit_events[-1].payload == {"text": "need more info"}


@pytest.mark.unit
def test_state_column_check_constraint_rejects_unknown_state(session: Session) -> None:
    """models.Request.state carries a DB-level CHECK constraint, so even raw
    SQL (bypassing the State enum) cannot persist an out-of-vocabulary state."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO approval_requests "
                "(requester, package, justification, state, created_at) "
                "VALUES ('a', 'p', 'j', 'garbage', CURRENT_TIMESTAMP)"
            )
        )


@pytest.mark.unit
def test_full_audit_trail_after_approval(svc: ApprovalService) -> None:
    """End-to-end check: every state transition is reflected in audit history."""
    req = svc.submit(requester="alice", package="x", justification="j")
    svc.comment(req.id, admin="bob", text="reviewing")
    svc.approve(req.id, admin="bob", reason="quota available")

    trail = [(e.event_type, e.actor) for e in req.audit_events]
    assert trail == [
        (EventType.SUBMITTED, "alice"),
        (EventType.COMMENT, "bob"),
        (EventType.APPROVED, "bob"),
    ]
