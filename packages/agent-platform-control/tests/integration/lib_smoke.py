"""Shared fixtures + helpers for E2E smoke harness (PR-H H-1).

Used by both:
  * ``test_e2e_software.py`` — pytest cassette form (fast, in-process, CI)
  * ``scripts/smoke/e2e_software_demo.py`` — out-of-process bash-like
    demo (slow, runs against a real running C1 / vcsim, staging)

The pytest fixtures here mirror the conventions in ``conftest.py`` but
drop the heavy ``client`` fixture in favour of an admin-flow client +
seeded tenant/users that the M1 acceptance criteria expect (see
``docs/architecture/acceptance/m1.md``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from agent_platform_control.app import create_app
from agent_platform_control.db import session as session_mod
from agent_platform_control.db.models import ImageVersion, Tenant, User
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

# Matches the dev-only admin token that agent_platform_control.auth accepts.
ADMIN_TOKEN = "dev-admin-token-CHANGE-ME"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

DEFAULT_TENANT_ID = "t-smoke"
DEFAULT_USER_ID = "alice"
# Matches ``deployment_payload``'s image_version. Registered as a legacy
# unsigned row (signature_b64 IS NULL) so the PR-E clone-path verifier
# (``_verify_image_version_or_fail``) treats it as registered-before-signing
# and accepts it — the seed contract the A1/A3/A5 happy path expects.
DEFAULT_IMAGE_VERSION = "v0.1.0"


@pytest_asyncio.fixture
async def smoke_seed(engine) -> async_sessionmaker:
    """Seed one tenant + one user + the default image version — minimal
    cast for the M1 happy path."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id=DEFAULT_TENANT_ID, display_name="Smoke Tenant"))
        s.add(
            User(
                id=DEFAULT_USER_ID,
                email=f"{DEFAULT_USER_ID}@smoke.test",
                display_name="Alice",
                tenant_id=DEFAULT_TENANT_ID,
            )
        )
        s.add(
            ImageVersion(
                version=DEFAULT_IMAGE_VERSION,
                ova_sha256="0" * 64,
                signature_b64=None,  # legacy unsigned → clone-path verifier accepts
                is_current=True,
            )
        )
        await s.commit()
    return sm


@pytest_asyncio.fixture
async def smoke_client(engine, smoke_seed) -> AsyncIterator[AsyncClient]:
    """ASGI client wired to the engine, seeded tenant/user ready."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine
    session_mod._sessionmaker = sm
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        yield c
    session_mod.reset_for_tests()


# ---------------------------------------------------------------- helpers


def deployment_payload(
    *, owner_id: str = DEFAULT_USER_ID, count: int = 1, tenant_id: str = DEFAULT_TENANT_ID
) -> dict[str, Any]:
    """Minimal valid POST /v1/deployments body for the smoke flow."""
    return {
        "tenant_id": tenant_id,
        "template": "[templates] smoke/smoke-ubuntu.vmtx",
        "image_version": DEFAULT_IMAGE_VERSION,
        "items": [
            {"owner_id": owner_id, "intended_name": f"vm-{owner_id}-{i:03d}"}
            for i in range(1, count + 1)
        ],
    }


async def submit_deployment(
    client: AsyncClient, *, count: int = 1, owner_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    """A1 assertion: POST /v1/deployments succeeds + returns deployment row.

    Raises ``AssertionError`` with a teaching message on failure — the
    smoke harness is a *test* tool, not a *fixer*, so we want explosions
    to surface bugs not get retried.
    """
    response = await client.post(
        "/v1/deployments",
        json=deployment_payload(count=count, owner_id=owner_id),
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201, (
        f"A1 FAILED — POST /v1/deployments → {response.status_code}\n"
        f"body: {response.text}\n"
        "fix: check seed_tenant + admin token wiring"
    )
    return response.json()


async def fetch_deployment(client: AsyncClient, deployment_id: str) -> dict[str, Any]:
    """A5 assertion building block: GET /v1/deployments/{id}."""
    response = await client.get(f"/v1/deployments/{deployment_id}", headers=ADMIN_HEADERS)
    assert response.status_code == 200, (
        f"A5 FAILED — GET /v1/deployments/{deployment_id} → "
        f"{response.status_code}\nbody: {response.text}"
    )
    return response.json()
