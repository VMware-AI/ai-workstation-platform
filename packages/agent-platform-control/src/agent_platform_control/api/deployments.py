"""/v1/deployments — batch VM provisioning.

Endpoints:
  POST   /v1/deployments              submit a batch
  GET    /v1/deployments              list (filter by tenant / state)
  GET    /v1/deployments/{id}         detail + per-item status
  POST   /v1/deployments/{id}/cancel  cancel pending items (in-flight + succeeded stay)

Tenant-scope bypass (Decision 14)
---------------------------------
All endpoints in this module are admin-only (``require_admin``) and may
read across tenants (the optional ``?tenant_id=`` query param is admin's
own filter, not an isolation boundary). Raw ``select(Deployment)`` here is
intentional — see ``scripts/check_tenant_scope.sh`` whitelist.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from agent_platform_approval.models import Request as ApprovalRequest
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.cloud_init import get_vm_secret_store
from ..auth import CurrentUser, require_admin
from ..config import get_settings
from ..db.models import Deployment, DeploymentItem, ImageVersion, Tenant, User
from ..db.session import get_session, get_sessionmaker
from ..orchestrator.approval_redeploy import revoke_deployment_for_approval
from ..orchestrator.audit import record_audit
from ..orchestrator.deployment_state import recompute_deployment_state
from ..orchestrator.protocol import Provisioner
from ..orchestrator.quota import QuotaExceededError, check_batch_quota
from ..orchestrator.tokens import encrypt_user_token, generate_user_token, hash_user_token
from ..package_specs import get_specs
from ..signing import SignatureVerificationError, verify_signature

logger = logging.getLogger(__name__)

# Marker for scripts/check_tenant_scope.sh — endpoints here are admin-only
# by construction; cross-tenant reads are required for the upgrade-orchestrator
# and audit views.
TENANT_SCOPE_ADMIN_BYPASS = True

router = APIRouter(prefix="/v1/deployments", tags=["deployments"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _validate_fk_targets_or_fail(
    session: AsyncSession, *, tenant_id: str, owner_ids: list[str]
) -> None:
    """Pre-check that the tenant and every owner exist before inserting.

    Without this an unknown tenant_id/owner_id either inserts an orphan row
    (SQLite, FK off) or raises a raw 500 (Postgres). We fail fast with a
    teaching 422 naming the bad id (PR-review #81 / #129).
    """
    if await session.get(Tenant, tenant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"tenant_id {tenant_id!r} is not a known tenant",
        )
    for owner_id in dict.fromkeys(owner_ids):  # de-dup, preserve order
        if await session.get(User, owner_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"owner_id {owner_id!r} is not a known user",
            )


async def _verify_image_version_or_fail(session: AsyncSession, version_label: str) -> None:
    """Decision 12 PR-E: every clone-bound image version must have a valid
    signature. Raises 422 on mismatch, 404 if the version isn't registered.

    Legacy rows (signature_b64 IS NULL) pass with a warning — they predate
    the migration; admins should re-register to upgrade.
    """
    row = (
        await session.execute(select(ImageVersion).where(ImageVersion.version == version_label))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"image_version {version_label!r} is not registered. "
                "Admin must POST /admin/image-versions with the OVA hash + "
                "signature before any deployment can clone it."
            ),
        )
    if row.signature_b64 is None:
        # Legacy unsigned row — log and accept (M1 fallback for in-flight
        # data from before PR-E). Not raised so existing tests + demos work.
        logger.warning(
            "image_version %r accepted with NULL signature (legacy/unsigned) — "
            "admin should re-register it with an OVA hash + signature",
            version_label,
        )
        return

    pubkey_pem = get_settings().image_signing_pubkey_pem.strip()
    if not pubkey_pem:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "image signing public key is not configured but the "
                "requested image_version carries a signature. Set "
                "AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM to enable verification."
            ),
        )

    try:
        verify_signature(
            public_key_pem=pubkey_pem.encode("utf-8"),
            signature_b64=row.signature_b64,
            message=row.ova_sha256.lower(),
        )
    except SignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(f"image_version {version_label!r} failed signature verification: {exc}"),
        ) from exc


# --------- request / response schemas ---------


class DeploymentItemIn(BaseModel):
    owner_id: str = Field(min_length=1, max_length=64)
    intended_name: str = Field(min_length=1, max_length=255)


class DeploymentCreate(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    template: str = Field(min_length=1, max_length=255)
    image_version: str = Field(min_length=1, max_length=64)
    items: list[DeploymentItemIn] = Field(min_length=1, max_length=200)


class DeploymentItemOut(BaseModel):
    id: int
    owner_id: str
    intended_name: str
    state: str
    vm_id: str | None
    error_message: str | None
    attempts: int


class DeploymentOut(BaseModel):
    id: str
    tenant_id: str
    template: str
    image_version: str
    state: str
    counts: dict[str, int]
    items: list[DeploymentItemOut] | None = None
    created_at: datetime
    updated_at: datetime


def _serialize(d: Deployment, include_items: bool = False) -> DeploymentOut:
    return DeploymentOut(
        id=d.id,
        tenant_id=d.tenant_id,
        template=d.template,
        image_version=d.image_version,
        state=d.state,
        counts={
            "requested": d.requested_count,
            "succeeded": d.succeeded_count,
            "failed": d.failed_count,
        },
        items=[
            DeploymentItemOut(
                id=i.id,
                owner_id=i.owner_id,
                intended_name=i.intended_name,
                state=i.state,
                vm_id=i.vm_id,
                error_message=i.error_message,
                attempts=i.attempts,
            )
            for i in d.items
        ]
        if include_items
        else None,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


# --------- endpoints ---------


@router.post("", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: DeploymentCreate,
    user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentOut:
    # Reject duplicate intended_names within the batch — catches obvious mistakes.
    names = [it.intended_name for it in body.items]
    if len(set(names)) != len(names):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="duplicate intended_name within batch",
        )

    # Validate tenant + owners exist before any insert so a typo yields a
    # teaching 422, not an orphan row or a raw 500 (PR-review #81).
    await _validate_fk_targets_or_fail(
        session,
        tenant_id=body.tenant_id,
        owner_ids=[it.owner_id for it in body.items],
    )

    # Decision 12 PR-E: verify the requested image_version is signed by the
    # configured trust root before we let any clones go out. Failure here
    # is friendlier than a vSphere clone of an untrusted OVA.
    await _verify_image_version_or_fail(session, body.image_version)

    # Decision 15 PR-E: per-user VM count quota check. Educational 429 so
    # the operator knows to recycle an existing VM (or raise the quota).
    try:
        await check_batch_quota(session, owner_ids=[it.owner_id for it in body.items])
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"User {exc.owner_id!r} has {exc.current} active VMs "
                f"(max {exc.limit}); requested {exc.extra_requested} more. "
                f"Decommission one via POST /api/me/instances/{{id}}/retire, "
                f"or raise the quota via admin (User.quota_vms or "
                f"Tenant.quota_vms_per_user)."
            ),
        ) from exc

    dep_id = uuid.uuid4().hex
    now = _utcnow()
    dep = Deployment(
        id=dep_id,
        tenant_id=body.tenant_id,
        requested_by=user.user_id,
        template=body.template,
        image_version=body.image_version,
        state="pending",
        requested_count=len(body.items),
        succeeded_count=0,
        failed_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(dep)

    for it in body.items:
        user_token = generate_user_token()
        session.add(
            DeploymentItem(
                deployment_id=dep_id,
                owner_id=it.owner_id,
                intended_name=it.intended_name,
                state="pending",
                attempts=0,
                user_token_enc=encrypt_user_token(user_token),
                user_token_hash=hash_user_token(user_token),
                created_at=now,
                updated_at=now,
            )
        )

    await session.commit()
    await session.refresh(dep, attribute_names=["items"])
    return _serialize(dep, include_items=True)


class ForceRedeployBody(BaseModel):
    """Double-confirm payload for ``?force=true`` (#216, doc 34 H-7)."""

    confirm: bool = False


def _provisioner_from_runtime(request: Request) -> Provisioner:
    """force-redeploy 需要 provisioner；worker 未启用给教学性 503。"""
    runtime = getattr(request.app.state, "runtime", None)
    worker = runtime.get("worker") if isinstance(runtime, dict) else None
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "force-redeploy 需要 provisioner：以 enable_worker=True + "
                "AGENT_PLATFORM_PROVISIONER_KIND 启动控制面后重试。"
            ),
        )
    return worker.provisioner


@router.post(
    "/from-approval/{request_id}",
    response_model=DeploymentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_approval(
    request_id: int,
    request: Request,
    force: bool = Query(
        default=False,
        description="销毁该审批此前制备的部署并重新制备（#216：必须配合 body confirm=true）",
    ),
    body: ForceRedeployBody | None = Body(default=None),
    user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentOut:
    """Provision a single-user VM from an APPROVED C13 request (Task 1.17.3).

    The approval carries only requester + package; tenant_id and image_version
    come from C1 config defaults (M1 is single-tenant). Idempotent — a second
    call for the same approval is rejected so an admin double-click cannot
    provision (and bill) two VMs.
    """
    approval = await session.get(ApprovalRequest, request_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Approval request {request_id} not found. "
                f"List requests via GET /admin/approvals/requests?state=pending."
            ),
        )

    if approval.state != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Approval request {request_id} is '{approval.state}', not 'approved'. "
                f"Approve it first via POST /admin/approvals/requests/{request_id}/approve."
            ),
        )

    # Package → vCenter template path comes from config/vm_package_specs.yaml
    # (decision 11), the single source of truth. The C13 approval form offers
    # exactly these packages, so an unknown value means the form and the specs
    # drifted apart.
    spec = get_specs().get(approval.package)
    if spec is None:
        known = ", ".join(get_specs().known_packages())
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown package '{approval.package}'. Known packages: {known}.",
        )
    template = spec.template

    # Capture the approval fields we still need below into plain locals: the
    # force path rolls back the request session (releasing its read lock before
    # revoke), which expires the ORM ``approval`` and would otherwise trigger a
    # lazy reload (MissingGreenlet) on later attribute access.
    approval_requester = approval.requester
    approval_package = approval.package

    if force:
        # 销毁用户 VM 不允许单次未确认 POST（#216，与 upgrades cutover/rollback
        # 的双重确认策略一致）：缺 body 或 confirm!=true 一律 409。
        if body is None or not body.confirm:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "force=true 会销毁该审批此前制备的 VM，必须在请求体中携带 "
                    '{"confirm": true} 双重确认（double-confirm policy）。'
                ),
            )
        provisioner = _provisioner_from_runtime(request)
        # AC1 (#353): revoke owns its own short transactions and destroys VMs
        # with no txn held. Release this request session's read lock first (the
        # approval validation above is done with) so it can't contend with
        # revoke's write commits on the same SQLite file.
        await session.rollback()
        revoked = await revoke_deployment_for_approval(
            get_sessionmaker(),
            provisioner,
            request_id,
            secret_store=get_vm_secret_store(),
        )
        record_audit(
            session,
            actor=user.user_id,
            operation="deployment.force_redeploy",
            resource=f"approval:{request_id}",
            # 单 VM 销毁失败由 revoke 内部逐条记 vm.destroy failure 审计行；
            # 此处 revoked_existing 只表示"是否找到并撤销了旧部署"。
            params={"revoked_existing": revoked},
            result="success",
        )
        # 有意为之的部分结果语义（doc 34 H-7）：撤销+审计先落库——销毁不可
        # 回滚、审计必须持久。若其后的重新制备失败（FK 422 / 并发撞 UNIQUE
        # 409），旧 VM 已销毁而新部署未建：重试同一 force 调用即可干净走通
        # （revoke 此时为 no-op）。
        await session.commit()

    # Idempotency: one approval provisions at most one *live* deployment. Match
    # on the approval_request_id column, which is cleared when a deployment is
    # cancelled — so cancelling genuinely frees the approval to be re-provisioned
    # once force-redeploy is wired (PR-review #129/I1). The UNIQUE index on that
    # column is the atomic backstop for a concurrent double-submit.
    existing = (
        await session.execute(
            select(Deployment).where(Deployment.approval_request_id == request_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Approval request {request_id} is already provisioned as live "
                f"deployment {existing.id}. Re-provision with ?force=true + "
                '{"confirm": true}（会销毁旧 VM）, or cancel it first.'
            ),
        )

    settings = get_settings()
    # The requester + default tenant become FK columns; validate they exist so
    # an approval from an unknown user (or an unseeded default tenant) yields a
    # teaching 422 rather than an orphan row / raw 500 (PR-review #129).
    await _validate_fk_targets_or_fail(
        session,
        tenant_id=settings.default_tenant_id,
        owner_ids=[approval_requester],
    )

    dep_id = uuid.uuid4().hex
    now = _utcnow()
    dep = Deployment(
        id=dep_id,
        tenant_id=settings.default_tenant_id,
        requested_by=user.user_id,
        template=template,
        image_version=settings.default_image_version,
        state="pending",
        requested_count=1,
        succeeded_count=0,
        failed_count=0,
        approval_request_id=request_id,
        extra={"approval_request_id": request_id, "source": "approval"},
        created_at=now,
        updated_at=now,
    )
    session.add(dep)

    user_token = generate_user_token()
    session.add(
        DeploymentItem(
            deployment_id=dep_id,
            owner_id=approval_requester,
            intended_name=f"{approval_requester}-{approval_package}",
            state="pending",
            attempts=0,
            user_token_enc=encrypt_user_token(user_token),
            user_token_hash=hash_user_token(user_token),
            created_at=now,
            updated_at=now,
        )
    )

    # The pre-check above is best-effort; the approval_request_id UNIQUE index
    # is the real guard against a concurrent double-submit creating two
    # deployments (PR-review #129 TOCTOU). A racing insert loses here → 409.
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Approval request {request_id} is already being provisioned "
                "(concurrent request). Retry the GET to find the deployment."
            ),
        ) from None
    await session.refresh(dep, attribute_names=["items"])
    return _serialize(dep, include_items=True)


@router.get("", response_model=list[DeploymentOut])
async def list_deployments(
    tenant_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[DeploymentOut]:
    stmt = select(Deployment).order_by(Deployment.created_at.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(Deployment.tenant_id == tenant_id)
    if state is not None:
        stmt = stmt.where(Deployment.state == state)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize(d, include_items=False) for d in rows]


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(
    deployment_id: str,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentOut:
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deployment not found")
    await session.refresh(dep, attribute_names=["items"])
    return _serialize(dep, include_items=True)


@router.get("/{deployment_id}/items/{item_id}/cloud-init-log")
async def get_item_cloud_init_log(
    deployment_id: str,
    item_id: int,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the cloud-init log captured for a single deployment item.

    Per docs/architecture/21 §3.1 (R-5). Used by the Deployment detail page
    to surface what cloud-init did inside the cloned VM. Source of truth is
    a per-item file written by the worker once PR-F merges (the worker uses
    vmware-aiops guest-ops to pull ``/var/log/cloud-init-output.log`` from
    the guest and dumps it locally). Until then the endpoint returns a
    structured ``available: false`` so the UI shows a graceful explanation
    rather than 404-ing.
    """
    from pathlib import Path

    item = await session.get(DeploymentItem, item_id)
    if item is None or item.deployment_id != deployment_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="deployment item not found"
        )

    # Convention: worker writes to
    # /var/lib/agent-platform-control/cloud-init-logs/<deployment>/<item>.log
    # (or AGENT_PLATFORM_CLOUD_INIT_LOG_DIR override for dev / tests).
    import os

    base = os.environ.get(
        "AGENT_PLATFORM_CLOUD_INIT_LOG_DIR", "/var/lib/agent-platform-control/cloud-init-logs"
    )
    log_path = Path(base) / deployment_id / f"{item_id}.log"

    if not log_path.is_file():
        return {
            "available": False,
            "deployment_id": deployment_id,
            "item_id": item_id,
            "vm_id": item.vm_id,
            "state": item.state,
            "reason": (
                "log_path not yet captured. Worker writes it after first successful "
                "guest-ops fetch (lands with PR-F)."
            ),
            "expected_path": str(log_path),
        }

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # SEC-17: the OSError string carries the server-side absolute path —
        # log it for the operator, return a generic detail to the caller.
        logger.error("cloud-init log unreadable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="cloud-init log could not be read",
        ) from exc

    return {
        "available": True,
        "deployment_id": deployment_id,
        "item_id": item_id,
        "vm_id": item.vm_id,
        "state": item.state,
        "bytes": len(content),
        "log": content[-8192:],  # tail 8 KB; full log is one button away in M2
        "truncated": len(content) > 8192,
        "path": str(log_path),
    }


@router.post("/{deployment_id}/cancel", response_model=DeploymentOut)
async def cancel_deployment(
    deployment_id: str,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentOut:
    """Cancel pending items. In-flight (cloning) and terminal (powered_on/failed)
    items are NOT touched — cancellation is best-effort, not destructive.
    """
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deployment not found")

    await session.refresh(dep, attribute_names=["items"])
    cancelled = 0
    for it in dep.items:
        if it.state == "pending":
            it.state = "cancelled"
            it.updated_at = _utcnow()
            cancelled += 1
    dep.cancelled_count += cancelled

    # Mark cancelled now so an in-flight item draining in the worker is not
    # requeued (PR-review #81 resurrection). The shared terminality function
    # then leaves it cancelled until the worker accounts for the last item.
    if cancelled and dep.state in {"pending", "running"}:
        dep.state = "cancelled"
        # Release the approval slot so the same approval can be re-provisioned
        # (the UNIQUE index would otherwise 409 forever) — PR-review I1. The
        # audit link survives in `extra`.
        dep.approval_request_id = None
    recompute_deployment_state(dep)

    await session.commit()
    return _serialize(dep, include_items=True)
