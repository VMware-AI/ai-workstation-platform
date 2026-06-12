"""End-to-end tests for the optional FastAPI router.

Spins up a TestClient backed by an in-memory SQLite session so the
HTTP surface is exercised against the same models a control-plane
deployment uses.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agent_platform_approval import Base
from agent_platform_approval.http import build_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client() -> Iterator[TestClient]:
    # StaticPool + shared cache so every dependency-injected Session
    # talks to the same in-memory SQLite, otherwise each FastAPI
    # request would get an empty database.
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def get_session() -> Iterator[Session]:
        with Session(engine) as s:
            yield s

    app = FastAPI()
    app.include_router(build_router(get_session))
    with TestClient(app) as c:
        yield c


def _submit(client: TestClient, *, requester: str = "alice", package: str = "qcoder") -> int:
    res = client.post(
        "/approvals/requests",
        json={"requester": requester, "package": package, "justification": "need it"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_submit_returns_pending_request(client: TestClient) -> None:
    res = client.post(
        "/approvals/requests",
        json={"requester": "alice", "package": "qcoder", "justification": "M0 eval"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["state"] == "pending"
    assert body["requester"] == "alice"
    assert body["decided_at"] is None


def test_submit_rejects_empty_justification(client: TestClient) -> None:
    res = client.post(
        "/approvals/requests",
        json={"requester": "alice", "package": "qcoder", "justification": ""},
    )
    assert res.status_code == 422


def test_list_filters_by_state(client: TestClient) -> None:
    rid = _submit(client)
    _submit(client, requester="bob")
    client.post(f"/approvals/requests/{rid}/approve", json={"admin": "admin1"})

    res = client.get("/approvals/requests", params={"state": "approved"})
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["id"] == rid


def test_get_includes_audit_history(client: TestClient) -> None:
    rid = _submit(client)
    client.post(f"/approvals/requests/{rid}/approve", json={"admin": "admin1", "reason": "ok"})
    client.post(f"/approvals/requests/{rid}/comment", json={"admin": "admin1", "text": "fyi"})

    res = client.get(f"/approvals/requests/{rid}")
    assert res.status_code == 200
    body = res.json()
    event_types = [e["event_type"] for e in body["audit_events"]]
    assert event_types == ["submitted", "approved", "comment"]


def test_approve_then_approve_returns_409(client: TestClient) -> None:
    rid = _submit(client)
    assert (
        client.post(f"/approvals/requests/{rid}/approve", json={"admin": "a1"}).status_code == 200
    )
    second = client.post(f"/approvals/requests/{rid}/approve", json={"admin": "a2"})
    assert second.status_code == 409


def test_reject_without_reason_is_422(client: TestClient) -> None:
    rid = _submit(client)
    res = client.post(f"/approvals/requests/{rid}/reject", json={"admin": "admin1"})
    assert res.status_code == 422


def test_reject_with_reason_succeeds(client: TestClient) -> None:
    rid = _submit(client)
    res = client.post(
        f"/approvals/requests/{rid}/reject",
        json={"admin": "admin1", "reason": "quota exceeded"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "rejected"
    assert body["decision_reason"] == "quota exceeded"


def test_get_unknown_id_is_404(client: TestClient) -> None:
    res = client.get("/approvals/requests/99999")
    assert res.status_code == 404


def test_comment_attaches_audit_event_without_state_change(client: TestClient) -> None:
    rid = _submit(client)
    res = client.post(
        f"/approvals/requests/{rid}/comment",
        json={"admin": "admin1", "text": "looking into it"},
    )
    assert res.status_code == 201
    assert res.json()["event_type"] == "comment"

    state_after = client.get(f"/approvals/requests/{rid}").json()["state"]
    assert state_after == "pending"
