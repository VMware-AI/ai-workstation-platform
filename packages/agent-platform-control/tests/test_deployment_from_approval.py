"""Task 1.17.3 — approved C13 request triggers a single-user C1 deployment.

The bridge endpoint ``POST /v1/deployments/from-approval/{request_id}``:
  * reads an approval Request (same DB, mounted by C13),
  * requires it to be in the terminal APPROVED state,
  * maps the free-text ``package`` to a vCenter template,
  * fills tenant_id / image_version from C1 config defaults (M1 single-tenant),
  * creates a 1-item deployment owned by the requester,
  * is idempotent: a second trigger for the same approval is rejected.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_platform_approval.models import Base as ApprovalBase
from agent_platform_approval.models import Request as ApprovalRequest
from agent_platform_approval.state import State
from agent_platform_control.db.models import Deployment, Tenant, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


@pytest_asyncio.fixture
async def approval_db(engine):
    """Create approval tables on the shared engine + seed tenant/requester.

    conftest's ``engine`` fixture only builds agent_platform_control's metadata;
    the approval tables live in a separate declarative Base, so we create
    them here on the same SQLite file.
    """
    async with engine.begin() as conn:
        await conn.run_sync(ApprovalBase.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="default", display_name="Default Tenant"))
        s.add(
            User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="default")
        )
        await s.commit()
    return sm


async def _insert_request(sm, *, requester="alice", package="agent-vm-small", state=State.APPROVED):
    async with sm() as s:
        req = ApprovalRequest(
            requester=requester,
            package=package,
            justification="need an agent box",
            state=state,
        )
        s.add(req)
        await s.commit()
        await s.refresh(req)
        return req.id


@pytest.mark.asyncio
async def test_from_approval_creates_single_user_deployment(client, approval_db):
    req_id = await _insert_request(approval_db, package="agent-vm-small")

    r = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["state"] == "pending"
    assert body["tenant_id"] == "default"
    assert body["image_version"] == "v0.1.0"
    assert body["counts"] == {"requested": 1, "succeeded": 0, "failed": 0}
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["owner_id"] == "alice"
    assert item["state"] == "pending"
    # template is derived from the package, not free-text
    assert "small" in body["template"]

    # reverse link recorded so the deployment is traceable to its approval
    async with approval_db() as s:
        dep = (await s.execute(select(Deployment))).scalars().one()
        assert dep.extra == {"approval_request_id": req_id, "source": "approval"}
        # the dedicated column is populated too — it carries the UNIQUE guard
        assert dep.approval_request_id == req_id


@pytest.mark.asyncio
async def test_from_approval_template_comes_from_package_specs_yaml(client, approval_db):
    """Regression (#352): the package → template mapping must come from
    config/vm_package_specs.yaml (get_specs, the single source of truth,
    decision 11) — not a second hardcoded dict in api/deployments.py. The
    template string must equal the yaml's verbatim, not a parallel copy."""
    from agent_platform_control.package_specs import get_specs

    spec = get_specs().get("agent-vm-medium")
    assert spec is not None, "agent-vm-medium must be defined in vm_package_specs.yaml"

    req_id = await _insert_request(approval_db, package="agent-vm-medium")
    r = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r.status_code == 201, r.text
    assert r.json()["template"] == spec.template


@pytest.mark.asyncio
async def test_approval_request_id_unique_constraint_blocks_double_insert(approval_db):
    """The UNIQUE index is the real TOCTOU guard (PR-review #129): a second
    deployment row for the same approval_request_id must fail at the DB."""
    from sqlalchemy.exc import IntegrityError

    def _dep(dep_id: str) -> Deployment:
        return Deployment(
            id=dep_id,
            tenant_id="default",
            requested_by="alice",
            template="t",
            image_version="v0.1.0",
            state="pending",
            requested_count=1,
            approval_request_id=4242,
        )

    async with approval_db() as s:
        s.add(_dep("dep-a"))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with approval_db() as s:
            s.add(_dep("dep-b"))  # same approval_request_id=4242
            await s.commit()


@pytest.mark.asyncio
async def test_from_approval_requires_admin(client, approval_db):
    req_id = await _insert_request(approval_db)

    r = await client.post(f"/v1/deployments/from-approval/{req_id}")
    assert r.status_code == 401

    r = await client.post(f"/v1/deployments/from-approval/{req_id}", headers={"X-User": "alice"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_from_approval_404_when_request_missing(client, approval_db):
    r = await client.post("/v1/deployments/from-approval/9999", headers=ADMIN)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_from_approval_409_when_not_approved(client, approval_db):
    req_id = await _insert_request(approval_db, state=State.PENDING)

    r = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r.status_code == 409
    # error must teach the operator how to fix it
    assert "approve" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_from_approval_422_on_unknown_package(client, approval_db):
    req_id = await _insert_request(approval_db, package="totally-bogus")

    r = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r.status_code == 422
    assert "agent-vm-small" in r.json()["detail"]  # lists known packages


@pytest.mark.asyncio
async def test_from_approval_is_idempotent(client, approval_db):
    req_id = await _insert_request(approval_db)

    r1 = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r1.status_code == 201

    r2 = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r2.status_code == 409
    assert "already" in r2.json()["detail"].lower()

    # exactly one deployment exists for the approval
    async with approval_db() as s:
        deps = (await s.execute(select(Deployment))).scalars().all()
        assert len(deps) == 1


@pytest.mark.asyncio
async def test_cancelling_frees_approval_for_reprovision(client, approval_db):
    """Cancelling an approval-created deployment releases its UNIQUE slot so the
    same approval can be provisioned again (PR-review I1)."""
    req_id = await _insert_request(approval_db)

    r1 = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r1.status_code == 201
    dep_id = r1.json()["id"]

    # Cancelling must null the approval_request_id column (slot released)…
    rc = await client.post(f"/v1/deployments/{dep_id}/cancel", headers=ADMIN)
    assert rc.status_code == 200
    async with approval_db() as s:
        dep = (await s.execute(select(Deployment))).scalars().one()
        assert dep.approval_request_id is None
        assert dep.state == "cancelled"

    # …so re-provisioning the same approval now succeeds instead of 409.
    r2 = await client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r2.status_code == 201, r2.text
