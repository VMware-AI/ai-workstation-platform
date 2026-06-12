"""FastAPI router that exposes :class:`ApprovalService` over HTTP.

This module is an *optional* surface — importing it requires the
``fastapi`` extra (``pip install agent-platform-approval[http]``). Pure
state-machine and service callers are unaffected.

The router is mountable: C1 agent-platform-control picks it up via
``app.include_router(build_router(...))`` and supplies the SQLAlchemy
session dependency. Keeping the dependency abstract lets the same
router work in a test app with an in-memory SQLite session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .errors import ConflictError, InvalidTransitionError, RequestNotFoundError
from .models import AuditEvent, EventType, Request
from .service import ApprovalService
from .state import State

logger = logging.getLogger(__name__)

SessionDependency = Callable[..., Session]
# Resolves the authenticated admin's identity server-side (e.g. from the host's
# session / bearer token). When supplied to build_router, the decision endpoints
# use THIS identity as the audit actor instead of trusting the request body —
# closing the self-asserted-decider gap (SEC-5). ``str | None`` matches the
# ``_identity`` fallback dependency built in ``build_router`` (None = no
# authorizer wired, body identity trusted).
AdminIdentityDependency = Callable[..., str | None]


class SubmitRequestIn(BaseModel):
    requester: str = Field(..., min_length=1, max_length=128)
    package: str = Field(..., min_length=1, max_length=64)
    justification: str = Field(..., min_length=1, max_length=2_000)


class DecideRequestIn(BaseModel):
    admin: str = Field(..., min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=2_000)


class CommentRequestIn(BaseModel):
    admin: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=2_000)


class AuditEventOut(BaseModel):
    id: int
    event_type: EventType
    actor: str
    at: datetime
    payload: Mapping[str, Any]

    @classmethod
    def from_orm_(cls, event: AuditEvent) -> AuditEventOut:
        return cls(
            id=event.id,
            event_type=event.event_type,
            actor=event.actor,
            at=event.at,
            payload=event.payload or {},
        )


class RequestOut(BaseModel):
    id: int
    requester: str
    package: str
    justification: str
    state: State
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    audit_events: list[AuditEventOut] = Field(default_factory=list)

    @classmethod
    def from_orm_(cls, req: Request, *, include_audit: bool = False) -> RequestOut:
        return cls(
            id=req.id,
            requester=req.requester,
            package=req.package,
            justification=req.justification,
            state=req.state,
            created_at=req.created_at,
            decided_at=req.decided_at,
            decided_by=req.decided_by,
            decision_reason=req.decision_reason,
            audit_events=(
                [AuditEventOut.from_orm_(e) for e in req.audit_events] if include_audit else []
            ),
        )


def build_router(
    get_session: SessionDependency,
    *,
    prefix: str = "/approvals",
    authorizer: AdminIdentityDependency | None = None,
) -> APIRouter:
    """Construct the FastAPI router.

    ``get_session`` is the caller's FastAPI ``Depends``-compatible
    session factory — typically a generator that yields a SQLAlchemy
    session and commits / rolls back on exit. The router calls
    ``session.commit()`` itself after every successful write so the
    factory can stay generic.

    ``authorizer`` (SEC-5) is an optional ``Depends``-compatible callable
    returning the authenticated user's identity. When supplied, the
    decide/comment endpoints record THAT identity as the audit actor and the
    submit endpoint records it as the requester — the request body's ``admin``
    / ``requester`` fields (which a caller could forge) are ignored; the
    decision endpoints also refuse self-approval (decider == requester). When
    ``None`` the body's values are used — keep that only for trusted internal
    callers / tests; document the trust boundary if mounting the router bare.
    """
    if authorizer is None:
        logger.warning(
            "agent-platform-approval router mounted WITHOUT an authorizer: "
            "running in untrusted identity mode — the request body's "
            "'requester' / 'admin' fields are taken at face value, so any "
            "caller can impersonate anyone (including dodging the "
            "self-approval ban). Pass authorizer=<identity dependency> to "
            "build_router() for any non-test deployment."
        )

    router = APIRouter(prefix=prefix, tags=["approvals"])

    # FastAPI dependency that yields the server-resolved admin identity, or None
    # when no authorizer was wired (body.admin is then used as before).
    _identity: Callable[..., str | None] = authorizer or (lambda: None)

    def _service(session: Session) -> ApprovalService:
        return ApprovalService(session)

    def _effective_admin(authed: str | None, body_admin: str) -> str:
        # Server-resolved identity wins; the body field is untrusted when an
        # authorizer is configured.
        return authed if authed is not None else body_admin

    def _forbid_self_decision(service: ApprovalService, request_id: int, admin: str) -> None:
        # SEC-5: an approver must not decide their own request. Raises
        # RequestNotFoundError (handled by callers) if the id is unknown.
        req = service.get(request_id)
        if req.requester == admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="an approver may not decide their own request",
            )

    @router.post("/requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
    def submit(
        body: SubmitRequestIn,
        session: Session = Depends(get_session),
        authed: str | None = Depends(_identity),
    ) -> RequestOut:
        service = _service(session)
        # SEC-5: with an authorizer wired, the requester is the server-resolved
        # identity — a forged body.requester could otherwise dodge the
        # self-approval ban (_forbid_self_decision compares requester == admin).
        req = service.submit(
            requester=authed if authed is not None else body.requester,
            package=body.package,
            justification=body.justification,
        )
        session.commit()
        session.refresh(req)
        return RequestOut.from_orm_(req)

    @router.get("/requests", response_model=list[RequestOut])
    def list_requests(
        state: State | None = Query(default=None),
        requester: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
    ) -> list[RequestOut]:
        service = _service(session)
        rows = service.list(state=state, requester=requester, limit=limit)
        return [RequestOut.from_orm_(r) for r in rows]

    @router.get("/requests/{request_id}", response_model=RequestOut)
    def get_request(
        request_id: int,
        session: Session = Depends(get_session),
    ) -> RequestOut:
        service = _service(session)
        try:
            req = service.get(request_id)
        except RequestNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return RequestOut.from_orm_(req, include_audit=True)

    @router.post("/requests/{request_id}/approve", response_model=RequestOut)
    def approve(
        request_id: int,
        body: DecideRequestIn,
        session: Session = Depends(get_session),
        authed: str | None = Depends(_identity),
    ) -> RequestOut:
        service = _service(session)
        admin = _effective_admin(authed, body.admin)
        try:
            _forbid_self_decision(service, request_id, admin)
            req = service.approve(request_id, admin=admin, reason=body.reason)
        except RequestNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (ConflictError, InvalidTransitionError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        session.commit()
        session.refresh(req)
        return RequestOut.from_orm_(req)

    @router.post("/requests/{request_id}/reject", response_model=RequestOut)
    def reject(
        request_id: int,
        body: DecideRequestIn,
        session: Session = Depends(get_session),
        authed: str | None = Depends(_identity),
    ) -> RequestOut:
        if not body.reason or not body.reason.strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="reject requires a non-empty reason",
            )
        service = _service(session)
        admin = _effective_admin(authed, body.admin)
        try:
            _forbid_self_decision(service, request_id, admin)
            req = service.reject(request_id, admin=admin, reason=body.reason)
        except RequestNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (ConflictError, InvalidTransitionError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        session.commit()
        session.refresh(req)
        return RequestOut.from_orm_(req)

    @router.post(
        "/requests/{request_id}/comment",
        response_model=AuditEventOut,
        status_code=status.HTTP_201_CREATED,
    )
    def comment(
        request_id: int,
        body: CommentRequestIn,
        session: Session = Depends(get_session),
        authed: str | None = Depends(_identity),
    ) -> AuditEventOut:
        service = _service(session)
        admin = _effective_admin(authed, body.admin)
        try:
            event = service.comment(request_id, admin=admin, text=body.text)
        except RequestNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        session.commit()
        session.refresh(event)
        return AuditEventOut.from_orm_(event)

    return router
