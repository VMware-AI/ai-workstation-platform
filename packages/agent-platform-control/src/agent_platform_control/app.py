"""FastAPI app factory + module-level `app` for uvicorn."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from agent_platform_approval.http import build_router as build_approval_router
from fastapi import Depends, FastAPI

from . import __version__
from .api import (
    admin,
    cloud_init,
    deployments,
    health,
    heartbeat,
    ingest,
    me,
    upgrades,
    version,
)
from .auth import CurrentUser, require_admin
from .config import get_settings
from .db.sync_session import get_sync_session
from .obs import setup_logging
from .request_id import RequestIdMiddleware
from .runtime import managed_runtime

logger = logging.getLogger("agent_platform_control")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(level=settings.log_level, json_format=settings.log_json)
    # Fail fast: refuse to boot a production posture (fake auth off) while any
    # committed dev secret is still in place (PR-review #57 / #81).
    problems = settings.production_safety_problems()
    if problems:
        raise RuntimeError(
            "refusing to start with insecure defaults: "
            + "; ".join(problems)
            + ". Set real values via env/.env (C18/Vaultwarden), or set "
            "AGENT_PLATFORM_ENABLE_FAKE_AUTH=1 for dev/test."
        )
    logger.info("agent-platform-control %s starting", __version__)
    async with managed_runtime(settings) as services:
        # Hand the started services to request handlers via app.state so the
        # /healthz/deep endpoint (F-2) can introspect status.
        app.state.runtime = services
        yield
    logger.info("agent-platform-control stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent-platform-control",
        version=__version__,
        description="Agent Platform C1 control plane — VM orchestration, RBAC, token accounting.",
        lifespan=lifespan,
    )
    # Correlation ID (harness H-12, #213): resolve/generate X-Request-ID,
    # stamp it on every log line via obs.JsonFormatter, echo on the response.
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router)
    app.include_router(version.router)
    app.include_router(admin.router)

    # C13 approval workflow. Sync router (sees its own engine) coexists
    # with the async main pool — FastAPI dispatches sync routes via a
    # threadpool. C2 console hits /admin/approvals/requests* directly.
    # Gated by require_admin: approve/reject/list change provisioning state,
    # so unauthenticated access is unacceptable (PR-review critical #104/#106).
    # SEC-5: pass the server-resolved admin identity as the audit actor so the
    # decision can't be attributed to a forged body `admin` field, and so a
    # requester can't approve their own request. require_admin still gates access.
    def _approval_admin_identity(user: CurrentUser = Depends(require_admin)) -> str:
        return user.user_id

    app.include_router(
        build_approval_router(
            get_sync_session,
            prefix="/admin/approvals",
            authorizer=_approval_admin_identity,
        ),
        dependencies=[Depends(require_admin)],
    )
    app.include_router(deployments.router)
    app.include_router(upgrades.router)
    app.include_router(me.router)
    app.include_router(cloud_init.router)
    app.include_router(ingest.router)
    app.include_router(heartbeat.router)
    return app


app = create_app()
