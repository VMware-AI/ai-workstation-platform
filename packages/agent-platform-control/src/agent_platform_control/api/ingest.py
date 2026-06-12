"""/api/ingest/* — service-to-service event ingest.

Currently exposes `POST /api/ingest/token-usage` for C5 (LiteLLM gateway)
to write one TokenUsage row per LLM call. Authenticated with a dedicated
service bearer (settings.c1_ingest_service_token), separate from the
admin token so a compromised gateway can't escalate to admin.

Per acceptance criterion 1.5.4: "一次调用对应一行".

Tenant-scope bypass (Decision 14)
---------------------------------
Authenticated by service bearer token, not a user identity. The event body
carries ``tenant_id`` directly (supplied by C5 gateway). This is a write,
not a tenant-scoped read, so :func:`db.tenant_scope.tenant_scope` does not
apply. Whitelisted in ``scripts/check_tenant_scope.sh``.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db.models import TokenUsage
from ..db.session import get_session

# Marker for scripts/check_tenant_scope.sh — see module docstring.
TENANT_SCOPE_ADMIN_BYPASS = True

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# Keep in sync with the adapter's installable agents (agent-platform-agent-adapter
# adapters/). xiaoguai (小怪) is the preferred default — omitting it 422'd its
# token-usage ingest. qcoder stays listed (frozen, not removed — see backlog note).
_AGENT_KINDS = {"xiaoguai", "goose", "claude-code", "qcoder"}


class TokenUsageEvent(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    agent: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=64)
    in_tokens: int = Field(ge=0)
    out_tokens: int = Field(ge=0)
    duration_s: float = Field(ge=0.0)
    ts: datetime | None = None
    # 128 + charset match the gateway's x-request-id contract (_SANE_REQUEST_ID
    # / request_id.py) so a valid 65–128-char id is not 422'd here (which would
    # drop the whole token_usage event). pattern re-rejects control chars at this
    # service boundary rather than trusting the gateway (defense-in-depth).
    correlation_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class IngestAck(BaseModel):
    id: int


def _require_service_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    # Compare on bytes — compare_digest raises TypeError on a non-ASCII str (M1).
    if not hmac.compare_digest(token.encode(), settings.c1_ingest_service_token.encode()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid service token",
        )


@router.post(
    "/token-usage",
    response_model=IngestAck,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_service_token)],
)
async def ingest_token_usage(
    event: TokenUsageEvent,
    session: AsyncSession = Depends(get_session),
) -> IngestAck:
    if event.agent not in _AGENT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"agent must be one of {sorted(_AGENT_KINDS)}, got {event.agent!r}",
        )
    row = TokenUsage(
        user_id=event.user_id,
        tenant_id=event.tenant_id,
        agent=event.agent,
        model=event.model,
        in_tokens=event.in_tokens,
        out_tokens=event.out_tokens,
        duration_s=event.duration_s,
        ts=event.ts or datetime.now(UTC),
        correlation_id=event.correlation_id,
    )
    session.add(row)
    # NOTE (doc 37 §4.4): the LLM-call audit record IS this token_usage row
    # (who=user_id / model / tokens / correlation_id / ts). We deliberately do
    # NOT also write an audit_view row per call — that table backs the
    # destructive-op feed (/admin/events) and a per-call duplicate would pollute
    # it + bloat unbounded with zero added signal (review #347 H-1/M-1).
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"user_id {event.user_id!r} or tenant_id {event.tenant_id!r} "
                "not found — ensure C5 maps the call to a known user/tenant "
                "before ingest"
            ),
        ) from None
    await session.refresh(row)
    return IngestAck(id=row.id)
