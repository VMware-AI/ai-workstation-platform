"""/admin/* — RBAC-protected inventory + image-version management.

Stub list endpoints (VMs / tenants / audit) are M1 placeholders; real
impl lands per task 1.3 + 1.11.

PR-E (decisions 12 + 13): image-version registration + promote. Both
require a signed registration request that ``signing.verify_signature``
accepts under the configured ``image_signing_pubkey_pem``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CurrentUser, require_admin
from ...config import get_settings
from ...db.models import ImageVersion
from ...db.session import get_session
from ...signing import SignatureVerificationError, verify_signature

router = APIRouter()


# ----------------------------------------------------------------- stubs


@router.get("/vms")
async def list_vms(user: CurrentUser = Depends(require_admin)) -> dict:
    """List all VMs across tenants. M1 stub — real impl pulls from db.models.VM."""
    return {"vms": [], "_stub": True, "caller": user.user_id}


@router.get("/tenants")
async def list_tenants(user: CurrentUser = Depends(require_admin)) -> dict:
    return {"tenants": [], "_stub": True}


@router.get("/audit")
async def list_audit(user: CurrentUser = Depends(require_admin), limit: int = 100) -> dict:
    return {"entries": [], "limit": limit, "_stub": True}


# ----------------------------------------------------------------- image_versions


class ImageVersionIn(BaseModel):
    """Body for ``POST /admin/image-versions`` (decision 12)."""

    version: str = Field(min_length=1, max_length=64)
    ova_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    signature_b64: str = Field(min_length=1, max_length=8192)
    signed_by: str | None = Field(default=None, max_length=255)
    template_path: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4096)


class ImageVersionOut(BaseModel):
    id: int
    version: str
    ova_sha256: str
    signed_by: str | None
    template_path: str | None
    notes: str | None
    is_current: bool
    created_at: datetime


def _serialize(v: ImageVersion) -> ImageVersionOut:
    return ImageVersionOut(
        id=v.id,
        version=v.version,
        ova_sha256=v.ova_sha256,
        signed_by=v.signed_by,
        template_path=v.template_path,
        notes=v.notes,
        is_current=v.is_current,
        created_at=v.created_at,
    )


def _verify_signature_or_fail(body: ImageVersionIn) -> None:
    """Educational 422 on signature failure, 503 if no key configured."""
    pubkey_pem = get_settings().image_signing_pubkey_pem.strip()
    if not pubkey_pem:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "image signing public key is not configured. Set "
                "AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM to a PEM-encoded "
                "SubjectPublicKeyInfo (RSA / ECDSA / Ed25519) to enable "
                "registration."
            ),
        )
    try:
        verify_signature(
            public_key_pem=pubkey_pem.encode("utf-8"),
            signature_b64=body.signature_b64,
            message=body.ova_sha256.lower(),
        )
    except SignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post(
    "/image-versions",
    response_model=ImageVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_image_version(
    body: ImageVersionIn,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ImageVersionOut:
    """Register a new image version after verifying its signature."""
    _verify_signature_or_fail(body)

    # Duplicate-version check (the unique index would also catch this, but
    # the 409 / "already registered" message is much friendlier than the
    # raw IntegrityError surfacing as 500).
    existing = (
        await session.execute(select(ImageVersion).where(ImageVersion.version == body.version))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"image version {body.version!r} is already registered as id={existing.id}. "
                "Use a new version label or DELETE the existing row first."
            ),
        )

    row = ImageVersion(
        version=body.version,
        ova_sha256=body.ova_sha256.lower(),
        signed_by=body.signed_by,
        signature_b64=body.signature_b64,
        template_path=body.template_path,
        notes=body.notes,
        is_current=False,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _serialize(row)


@router.post("/image-versions/{image_version_id}/promote", response_model=ImageVersionOut)
async def promote_image_version(
    image_version_id: int,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ImageVersionOut:
    """Set ``is_current=True`` on the given image version (decision 13).

    M1 scope is **global** — at most one ``is_current=True`` row across the
    entire platform. Same-tx atomic flip: every other row's
    ``is_current`` is cleared first.
    """
    target = await session.get(ImageVersion, image_version_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"image version id={image_version_id} not found. "
                "List candidates via GET /admin/image-versions."
            ),
        )

    # Demote everyone else (skip ourselves to keep the write count predictable).
    await session.execute(
        update(ImageVersion).where(ImageVersion.id != image_version_id).values(is_current=False)
    )
    target.is_current = True
    await session.commit()
    await session.refresh(target)
    return _serialize(target)


@router.get("/image-versions", response_model=list[ImageVersionOut])
async def list_image_versions(
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[ImageVersionOut]:
    """List every image version (newest first)."""
    stmt = select(ImageVersion).order_by(ImageVersion.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]
