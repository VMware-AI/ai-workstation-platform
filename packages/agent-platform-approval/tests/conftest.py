from __future__ import annotations

from collections.abc import Iterator

import pytest
from agent_platform_approval import ApprovalService, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session per test; rolled back at teardown."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def svc(session: Session) -> ApprovalService:
    return ApprovalService(session)
