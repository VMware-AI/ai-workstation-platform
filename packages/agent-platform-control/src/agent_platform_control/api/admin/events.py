"""/admin/events — recent audit-log entries for the Overview feed."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import AuditView
from ...db.session import get_session

router = APIRouter()


@router.get("/events")
async def list_events(
    session: AsyncSession = Depends(get_session),
    since: datetime | None = Query(default=None, description="ISO8601 lower bound for ts"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Return the most recent ``AuditView`` rows.

    ``since`` filters rows whose timestamp is on/after the given instant;
    defaults to no lower bound (return latest ``limit`` rows). ``AuditView``
    is the control plane's own audit log, written by
    :func:`orchestrator.audit.record_audit` on destructive ops (VM destroy /
    force-redeploy). LLM-call metering is NOT audited here — that lives in the
    ``token_usage`` table (which is itself the per-call audit record; doc 37 §4.4).
    """
    stmt = select(AuditView).order_by(AuditView.ts.desc()).limit(limit)
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        stmt = stmt.where(AuditView.ts >= since)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "events": [
            {
                "id": r.id,
                "actor": r.actor,
                "operation": r.operation,
                "resource": r.resource,
                "result": r.result,
                "ts": r.ts.isoformat() if r.ts else None,
                "params": r.params,
            }
            for r in rows
        ],
        "limit": limit,
        "since": since.isoformat() if since else None,
    }
