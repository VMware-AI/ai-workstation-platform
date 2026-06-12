"""SEC-5: when an authorizer is wired, the server-resolved identity is the audit
actor AND the requester (the request body's ``admin`` / ``requester`` are
ignored), and an approver may not decide their own request."""

from __future__ import annotations

from collections.abc import Iterator

from agent_platform_approval import Base
from agent_platform_approval.http import build_router
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def _header_authorizer(x_identity: str = Header()) -> str:
    """Test stand-in for a real authorizer: identity comes from a header so
    each request can act as a different authenticated principal."""
    return x_identity


def _make_client(*, with_authorizer: bool = True) -> TestClient:
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

    authorizer = _header_authorizer if with_authorizer else None
    app = FastAPI()
    app.include_router(build_router(get_session, authorizer=authorizer))
    return TestClient(app)


def _submit(client: TestClient, *, identity: str, body_requester: str = "alice") -> int:
    res = client.post(
        "/approvals/requests",
        json={"requester": body_requester, "package": "qcoder", "justification": "need it"},
        headers={"X-Identity": identity},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_authorizer_identity_overrides_forged_body_admin() -> None:
    client = _make_client()
    rid = _submit(client, identity="alice")
    # caller forges admin="ceo" in the body; the audit actor must be the
    # server-resolved "real-admin".
    res = client.post(
        f"/approvals/requests/{rid}/approve",
        json={"admin": "ceo"},
        headers={"X-Identity": "real-admin"},
    )
    assert res.status_code == 200, res.text
    audit = client.get(f"/approvals/requests/{rid}", headers={"X-Identity": "real-admin"}).json()[
        "audit_events"
    ]
    actors = [e["actor"] for e in audit if e["event_type"] == "approved"]
    assert actors == ["real-admin"]


def test_authorizer_identity_overrides_forged_body_requester() -> None:
    """SEC-5 submit side: the stored requester is the server-resolved identity,
    not whatever the body claims."""
    client = _make_client()
    rid = _submit(client, identity="alice", body_requester="ceo")
    body = client.get(f"/approvals/requests/{rid}", headers={"X-Identity": "alice"}).json()
    assert body["requester"] == "alice"
    submitted_actors = [e["actor"] for e in body["audit_events"] if e["event_type"] == "submitted"]
    assert submitted_actors == ["alice"]


def test_forged_requester_cannot_dodge_self_approval_ban() -> None:
    """Submitting with a forged body requester must not let the same principal
    approve their own request later."""
    client = _make_client()
    rid = _submit(client, identity="alice", body_requester="someone-else")
    res = client.post(
        f"/approvals/requests/{rid}/approve",
        json={"admin": "alice"},
        headers={"X-Identity": "alice"},
    )
    assert res.status_code == 403


def test_self_approval_is_forbidden() -> None:
    client = _make_client()
    rid = _submit(client, identity="alice")
    res = client.post(
        f"/approvals/requests/{rid}/approve",
        json={"admin": "alice"},
        headers={"X-Identity": "alice"},
    )
    assert res.status_code == 403


def test_self_rejection_is_forbidden() -> None:
    client = _make_client()
    rid = _submit(client, identity="alice")
    res = client.post(
        f"/approvals/requests/{rid}/reject",
        json={"admin": "alice", "reason": "nope"},
        headers={"X-Identity": "alice"},
    )
    assert res.status_code == 403


def test_no_authorizer_keeps_body_requester_and_admin() -> None:
    # backward-compat: without an authorizer the body's requester / admin are
    # trusted as-is (build_router logs a warning about that mode).
    client = _make_client(with_authorizer=False)
    res = client.post(
        "/approvals/requests",
        json={"requester": "alice", "package": "qcoder", "justification": "need it"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["requester"] == "alice"
    rid = res.json()["id"]

    res = client.post(f"/approvals/requests/{rid}/approve", json={"admin": "bob"})
    assert res.status_code == 200, res.text
    audit = client.get(f"/approvals/requests/{rid}").json()["audit_events"]
    actors = [e["actor"] for e in audit if e["event_type"] == "approved"]
    assert actors == ["bob"]
