"""SQLAlchemy 2.x declarative models for approval workflow + audit trail."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .state import State


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base. C1 control-plane mounts these tables into its Alembic env."""


class EventType(StrEnum):
    """Audit event categories.

    SUBMITTED, APPROVED, REJECTED match the lifecycle 1:1; COMMENT is reserved
    for free-form admin notes that don't change state.
    """

    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMMENT = "comment"


class Request(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    requester: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    package: Mapped[str] = mapped_column(String(64), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[State] = mapped_column(String(16), nullable=False, default=State.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="AuditEvent.at",
    )

    __table_args__ = (
        Index("ix_approval_requests_state_created", "state", "created_at"),
        # Belt for the bare String(16) column: even raw SQL cannot persist an
        # out-of-vocabulary state. The vocabulary is GENERATED from the State
        # enum (single source of truth) so the two can't drift — add a State
        # value and the constraint widens automatically. Tests/dev use
        # Base.metadata.create_all so it takes effect immediately; C1's Alembic
        # env picks it up on its next autogenerate.
        CheckConstraint(
            f"state IN ({', '.join(repr(s.value) for s in State)})",
            name="ck_approval_requests_state",
        ),
    )


class AuditEvent(Base):
    __tablename__ = "approval_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[EventType] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    request: Mapped[Request] = relationship(back_populates="audit_events")
