"""#216 — force-redeploy 必须双重确认（doc 34 P1 H-7 correction）.

``POST /v1/deployments/from-approval/{id}?force=true``：
  * body 不带 ``confirm: true`` → 409（销毁用户 VM 不允许单次未确认 POST）
  * 确认后：销毁旧部署的 VM、释放审批位、重新制备，全程审计
  * worker（provisioner）未启用 → 503 教学性错误
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from agent_platform_approval.models import Base as ApprovalBase
from agent_platform_approval.models import Request as ApprovalRequest
from agent_platform_approval.state import State
from agent_platform_control.db.models import AuditView, Deployment, DeploymentItem, Tenant, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


class RecordingProvisioner:
    def __init__(self) -> None:
        self.destroyed: list[str] = []

    async def clone_vm(self, spec):  # pragma: no cover - 不在本测试路径
        raise AssertionError("clone_vm must not be called here")

    async def destroy_vm(self, vm_id: str) -> None:
        self.destroyed.append(vm_id)


@pytest_asyncio.fixture
async def env(engine, monkeypatch):
    """app + client + provisioner 可注入（lifespan 在 ASGITransport 下不跑）。"""
    from agent_platform_control.app import create_app
    from agent_platform_control.db import session as session_mod
    from httpx import ASGITransport, AsyncClient

    sm = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine
    session_mod._sessionmaker = sm
    async with engine.begin() as conn:
        await conn.run_sync(ApprovalBase.metadata.create_all)
    async with sm() as s:
        s.add(Tenant(id="default", display_name="Default Tenant"))
        s.add(
            User(id="alice", email="alice@example.com", display_name="Alice", tenant_id="default")
        )
        await s.commit()

    app = create_app()
    provisioner = RecordingProvisioner()
    app.state.runtime = {"worker": SimpleNamespace(provisioner=provisioner)}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield SimpleNamespace(client=c, app=app, sm=sm, provisioner=provisioner)
    session_mod.reset_for_tests()
    from agent_platform_control.api.cloud_init import reset_vm_secret_store_for_tests

    reset_vm_secret_store_for_tests()


async def _approved_request(sm) -> int:
    async with sm() as s:
        req = ApprovalRequest(
            requester="alice",
            package="agent-vm-small",
            justification="x",
            state=State.APPROVED,
        )
        s.add(req)
        await s.commit()
        await s.refresh(req)
        return req.id


async def _provision(env, req_id: int):
    r = await env.client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


async def _bind_vm(sm, deployment_id: str, vm_id: str) -> None:
    async with sm() as s:
        item = (
            (
                await s.execute(
                    select(DeploymentItem).where(DeploymentItem.deployment_id == deployment_id)
                )
            )
            .scalars()
            .one()
        )
        item.vm_id = vm_id
        await s.commit()


@pytest.mark.asyncio
async def test_force_without_confirm_is_409_and_destroys_nothing(env):
    req_id = await _approved_request(env.sm)
    await _provision(env, req_id)

    r = await env.client.post(f"/v1/deployments/from-approval/{req_id}?force=true", headers=ADMIN)
    assert r.status_code == 409
    assert "confirm" in r.json()["detail"]
    assert env.provisioner.destroyed == []

    r = await env.client.post(
        f"/v1/deployments/from-approval/{req_id}?force=true",
        headers=ADMIN,
        json={"confirm": False},
    )
    assert r.status_code == 409
    assert env.provisioner.destroyed == []


@pytest.mark.asyncio
async def test_force_with_confirm_revokes_and_reprovisions_with_audit(env):
    req_id = await _approved_request(env.sm)
    first = await _provision(env, req_id)
    await _bind_vm(env.sm, first["id"], "vm-old-1")

    r = await env.client.post(
        f"/v1/deployments/from-approval/{req_id}?force=true",
        headers=ADMIN,
        json={"confirm": True},
    )
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["id"] != first["id"]

    assert env.provisioner.destroyed == ["vm-old-1"]
    async with env.sm() as s:
        old = await s.get(Deployment, first["id"])
        assert old.state == "cancelled"
        assert old.approval_request_id is None
        new = await s.get(Deployment, second["id"])
        assert new.approval_request_id == req_id
        ops = [row.operation for row in (await s.execute(select(AuditView))).scalars().all()]
        assert "deployment.force_redeploy" in ops
        assert "vm.destroy" in ops


@pytest.mark.asyncio
async def test_plain_repeat_without_force_still_409(env):
    req_id = await _approved_request(env.sm)
    await _provision(env, req_id)
    r = await env.client.post(f"/v1/deployments/from-approval/{req_id}", headers=ADMIN)
    assert r.status_code == 409
    assert "force=true" in r.json()["detail"]


@pytest.mark.asyncio
async def test_force_without_worker_is_teaching_503(env):
    req_id = await _approved_request(env.sm)
    await _provision(env, req_id)
    env.app.state.runtime = {"worker": None}
    r = await env.client.post(
        f"/v1/deployments/from-approval/{req_id}?force=true",
        headers=ADMIN,
        json={"confirm": True},
    )
    assert r.status_code == 503
    assert "enable_worker" in r.json()["detail"]
    assert env.provisioner.destroyed == []


@pytest.mark.asyncio
async def test_force_on_unprovisioned_approval_just_provisions(env):
    # 没有旧部署时 force 等价于普通制备（revoke no-op，照常 201）
    req_id = await _approved_request(env.sm)
    r = await env.client.post(
        f"/v1/deployments/from-approval/{req_id}?force=true",
        headers=ADMIN,
        json={"confirm": True},
    )
    assert r.status_code == 201, r.text
    assert env.provisioner.destroyed == []


@pytest.mark.asyncio
async def test_force_on_non_approved_state_409_before_any_destruction(env):
    # 安全不变量回归锁定（#216 review）：状态检查必须先于销毁——pending
    # 审批带 force+confirm 也不得碰 provisioner。重构挪动 force 块即破测。
    async with env.sm() as s:
        req = ApprovalRequest(
            requester="alice", package="agent-vm-small", justification="x", state=State.PENDING
        )
        s.add(req)
        await s.commit()
        await s.refresh(req)
    r = await env.client.post(
        f"/v1/deployments/from-approval/{req.id}?force=true",
        headers=ADMIN,
        json={"confirm": True},
    )
    assert r.status_code == 409
    assert "approved" in r.json()["detail"]
    assert env.provisioner.destroyed == []
