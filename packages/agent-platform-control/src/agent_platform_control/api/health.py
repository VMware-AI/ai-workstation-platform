"""/healthz + /readyz + /healthz/deep (PR-F F-2)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, require_admin
from ..db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Ready = DB reachable. Returns 503 when the DB is down.

    Readiness probes (k8s / LB) gate on the HTTP status code, so a DB-down
    instance must not return 200. The raw exception is logged server-side
    only — never returned to the (unauthenticated) caller.
    """
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readyz: database probe failed")
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready", "db": "ok"})


@router.get("/healthz/deep")
async def healthz_deep(
    request: Request,
    _user: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Detailed runtime status — admin-gated since it surfaces internal state.

    Surfaces:
      * db — DB ping
      * worker — DeploymentWorker status (running / disabled)
      * cleanup_cron / heartbeat_sweeper / token_expiry_sweeper — placeholders
        until PR-D (#136) merges and the runtime wiring picks them up
    """
    components: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        components["db"] = "ok"
    except Exception as e:
        components["db"] = f"{type(e).__name__}: {e}"

    runtime = getattr(request.app.state, "runtime", {}) or {}
    worker = runtime.get("worker")
    if worker is None:
        # "failed" (wanted via enable_worker but _build_worker raised at startup,
        # e.g. a bad TTYD_ALLOW_CIDR) is distinct from "disabled" (deliberately
        # off) so a misconfig surfaces as degraded, not a healthy-looking silent
        # no-op where deployments queue forever (review MEDIUM).
        components["worker"] = (
            f"failed: {runtime['worker_error']}" if runtime.get("worker_error") else "disabled"
        )
    elif getattr(worker, "_task", None) is None:
        components["worker"] = "stopped"
    else:
        components["worker"] = "running"

    # Heartbeat sweeper is wired (#350): report its real lifecycle so an
    # operator can tell a running sweeper from a crashed one. It rides the
    # worker's enable gate, so "disabled" when the worker is off.
    sweeper = runtime.get("heartbeat_sweeper")
    if sweeper is None:
        components["heartbeat_sweeper"] = "disabled"
    elif getattr(sweeper, "_task", None) is None:
        components["heartbeat_sweeper"] = "stopped"
    else:
        components["heartbeat_sweeper"] = "running"

    # Cleanup cron + token-expiry sweeper slots are not yet wired (decision 5
    # cleanup cron destroys customer VMs — gated on its recovery runbook).
    # Report as "pending" so an operator knows the slot exists but isn't running
    # yet, vs "disabled" which means deliberately off.
    components["cleanup_cron"] = "pending-pr-d"
    components["token_expiry_sweeper"] = "pending-pr-d"  # noqa: S105 — state name, not a credential  # nosec B105 — state label, not a credential

    # A running worker is healthy. "stopped" (task gone) stays degraded so a
    # crashed worker still surfaces (PR-review #143).
    healthy = {"ok", "running"}
    overall = (
        "ok"
        if all(v in healthy or v.startswith(("disabled", "pending")) for v in components.values())
        else "degraded"
    )
    return {"status": overall, "components": components}
