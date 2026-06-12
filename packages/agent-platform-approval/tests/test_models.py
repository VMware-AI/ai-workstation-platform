"""Model-level tests for the approval schema — focus on the state CheckConstraint.

The CheckConstraint vocabulary is generated from the ``State`` enum so the
two can never drift (#355 item 5): add a value to ``State`` and the DB
constraint widens to match automatically, with no second hand-maintained list.
"""

from __future__ import annotations

import pytest
from agent_platform_approval import Base, Request, State
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _state_check_sql() -> str:
    """The SQL text of the approval_requests state CheckConstraint."""
    table = Request.__table__
    constraint = next(
        c for c in table.constraints if getattr(c, "name", None) == "ck_approval_requests_state"
    )
    return str(constraint.sqltext)


@pytest.mark.unit
def test_state_constraint_is_generated_from_enum() -> None:
    """Every State value (and nothing else) must appear in the constraint —
    proving the vocabulary is enum-derived, not a hand-copied literal list."""
    sql = _state_check_sql()
    for member in State:
        assert repr(member.value) in sql, f"{member.value!r} missing from constraint"
    # Exactly one quoted literal per enum member (no stale extras).
    assert sql.count("'") == 2 * len(list(State))


@pytest.mark.unit
def test_constraint_accepts_every_enum_value() -> None:
    """A regression guard via create_all: each in-vocabulary state inserts
    cleanly. If a future State value were added but the constraint NOT
    regenerated, this would start failing — the drift would be caught."""
    engine = create_engine("sqlite:///:memory:", future=True)
    # SQLite enforces CHECK constraints only with foreign_keys/we must ensure
    # CHECK is on (it is by default for table CHECKs in modern SQLite).
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for member in State:
            s.add(
                Request(
                    requester="alice",
                    package="pkg",
                    justification="because",
                    state=member,
                )
            )
        s.commit()
        assert s.query(Request).count() == len(list(State))


@pytest.mark.unit
def test_constraint_rejects_out_of_vocabulary_state() -> None:
    """Raw SQL cannot smuggle an unknown state past the CHECK constraint."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO approval_requests "
                "(requester, package, justification, state, created_at) "
                "VALUES ('a', 'p', 'j', 'bogus', '2026-01-01T00:00:00+00:00')"
            )
        )
