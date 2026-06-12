"""C13 router is mounted at /admin/approvals.

End-to-end approval workflow is exercised in agent-platform-approval's own
test suite — here we only assert that the mount itself is wired and
the routes are discoverable on the live FastAPI app.
"""

from __future__ import annotations

import pytest
from agent_platform_control.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_approval_endpoints_require_admin() -> None:
    """C13 approval mutations must reject unauthenticated callers (no admin token).

    Regression for PR-review critical #104/#106: the router was mounted under
    /admin without require_admin, leaving approve/reject/list open to anyone.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No Authorization header → must be 401, not 200/404/500.
        for method, path in (
            ("get", "/admin/approvals/requests"),
            ("post", "/admin/approvals/requests/req-1/approve"),
            ("post", "/admin/approvals/requests/req-1/reject"),
        ):
            resp = await client.request(method, path, json={})
            assert resp.status_code == 401, (
                f"{method.upper()} {path} returned {resp.status_code}, expected 401 "
                "(approval routes must be admin-gated)"
            )


def test_approval_routes_are_mounted() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    expected = {
        "/admin/approvals/requests",
        "/admin/approvals/requests/{request_id}",
        "/admin/approvals/requests/{request_id}/approve",
        "/admin/approvals/requests/{request_id}/reject",
        "/admin/approvals/requests/{request_id}/comment",
    }
    missing = expected - paths
    assert not missing, f"missing approval routes: {sorted(missing)}"


def test_approval_router_is_tagged_for_openapi() -> None:
    """Sanity-check that OpenAPI docs surface the approvals group."""
    app = create_app()
    schema = app.openapi()
    tags = {
        tag
        for op in schema["paths"].values()
        for method in op.values()
        for tag in method.get("tags", [])
    }
    assert "approvals" in tags
