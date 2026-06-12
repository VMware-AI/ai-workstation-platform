"""Auth dependencies — M1 stub.

Real Keycloak OIDC wiring is M1.2.3; today we accept:
  - Header `Authorization: Bearer <token>` matching settings.admin_api_token → admin
  - Header `X-User: alice` (when enable_fake_auth) → user 'alice', role 'user'

For X-User fake-auth, the caller's ``tenant_id`` is looked up from the
``users`` table so :mod:`db.tenant_scope` can filter cross-tenant reads
(Decision 14). Unknown users still resolve as ``tenant_id=None`` — the
endpoint is then responsible for refusing tenant-scoped reads.

Tests use the same code path; production deployments MUST flip enable_fake_auth=False
and provide a real OIDC issuer before exposing /admin/*.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db.models import User
from .db.session import get_session


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    role: str  # "admin" | "user" | "tenant-admin"
    tenant_id: str | None = None


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """Resolve caller. Raises 401 if no credential matches.

    For fake-auth ``X-User`` callers, the user's ``tenant_id`` is loaded from
    the ``users`` table so downstream :mod:`db.tenant_scope` calls have a
    non-empty tenant id. If the user row is missing, ``tenant_id`` is left
    ``None`` — tenant_scope() then refuses, surfacing the misconfiguration
    instead of silently leaking.
    """
    token = _bearer_token(authorization)
    # Compare on bytes: compare_digest raises TypeError on a non-ASCII str,
    # which would surface as a 500 instead of a clean 401 (PR-review M1).
    if token and hmac.compare_digest(token.encode(), settings.admin_api_token.encode()):
        return CurrentUser(user_id="admin", role="admin")

    if settings.enable_fake_auth and x_user:
        # Resolve tenant_id so tenant_scope can filter.
        # Unknown user → tenant_id stays None; endpoints that require it
        # will fail loudly via TenantScopeError.
        user_row = await session.get(User, x_user)
        tenant_id = user_row.tenant_id if user_row is not None else None
        return CurrentUser(user_id=x_user, role="user", tenant_id=tenant_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"admin role required, got {user.role}",
        )
    return user
